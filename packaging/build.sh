#!/bin/bash
#
# Builds, signs, notarises and packages Authoring Assistant.app.
#
# Run this on a development Mac. The author never runs it, and never needs any
# of the tools it uses. See BUILD.md for the full instructions.
#
#   ./packaging/build.sh                 build, sign, notarise, staple, package
#   ./packaging/build.sh --no-notarize   build and sign only (quick local check)
#   ./packaging/build.sh --no-sign       build only (no certificate needed)
#
set -euo pipefail

# --- settings ----------------------------------------------------------------

APP_NAME="Authoring Assistant"
EXECUTABLE="AuthoringAssistant"
BUNDLE_ID="${BUNDLE_ID:-com.alecgordon.authoring-assistant}"
VERSION="${VERSION:-1.0.0}"
BUILD_NUMBER="${BUILD_NUMBER:-$(date +%Y%m%d%H%M)}"
COPYRIGHT="${COPYRIGHT:-}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
ARCH="${ARCH:-$(uname -m)}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
CACHE="$ROOT/build/cache"
APP="$BUILD/$APP_NAME.app"

DO_SIGN=1
DO_NOTARIZE=1
for arg in "$@"; do
  case "$arg" in
    --no-sign)     DO_SIGN=0; DO_NOTARIZE=0 ;;
    --no-notarize) DO_NOTARIZE=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
die()  { printf '\n\033[31mError: %s\033[0m\n' "$1" >&2; exit 1; }

# --- the signing identity ----------------------------------------------------
# Never hardcoded. Taken from the environment, or asked for, or chosen from the
# certificates already in the keychain.

if [ "$DO_SIGN" -eq 1 ]; then
  if [ -z "${SIGN_IDENTITY:-}" ]; then
    # Written the long way round: macOS still ships bash 3.2, which has no
    # mapfile builtin.
    FOUND=()
    while IFS= read -r line; do
      [ -n "$line" ] && FOUND+=("$line")
    done < <(security find-identity -v -p codesigning 2>/dev/null \
      | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' || true)
    if [ "${#FOUND[@]}" -eq 1 ]; then
      SIGN_IDENTITY="${FOUND[0]}"
      note "Using the only Developer ID found: $SIGN_IDENTITY"
    elif [ "${#FOUND[@]}" -gt 1 ]; then
      echo "More than one Developer ID Application certificate is installed:"
      for i in "${!FOUND[@]}"; do echo "  $((i+1))) ${FOUND[$i]}"; done
      read -r -p "Which one? [1] " choice
      choice="${choice:-1}"
      SIGN_IDENTITY="${FOUND[$((choice-1))]}"
    else
      read -r -p "Developer ID Application identity: " SIGN_IDENTITY
    fi
  fi
  [ -n "${SIGN_IDENTITY:-}" ] || die "No signing identity given."
fi

# --- a clean tree ------------------------------------------------------------

step "Preparing"
rm -rf "$APP" "$DIST"
mkdir -p "$BUILD" "$DIST" "$CACHE"
note "version $VERSION (build $BUILD_NUMBER), $ARCH"

# --- the bundled interpreter -------------------------------------------------
# Downloaded here at build time so that the finished application carries its own
# Python and the author needs nothing installed at all.

step "Fetching a relocatable Python"
case "$ARCH" in
  arm64)  PY_TRIPLE="aarch64-apple-darwin" ;;
  x86_64) PY_TRIPLE="x86_64-apple-darwin" ;;
  *) die "Unsupported architecture: $ARCH" ;;
esac

PY_RELEASE="${PYTHON_BUILD_STANDALONE_RELEASE:-}"
if [ -z "$PY_RELEASE" ]; then
  PY_RELEASE=$(curl -fsSL \
    "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest" \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
fi
[ -n "$PY_RELEASE" ] || die "Could not work out the latest Python release."

PY_ASSET=$(curl -fsSL \
  "https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/$PY_RELEASE" \
  | sed -n 's/.*"browser_download_url": *"\([^"]*\)".*/\1/p' \
  | grep "cpython-${PYTHON_VERSION}\..*-${PY_TRIPLE}-install_only\.tar\.gz$" \
  | head -1)
[ -n "$PY_ASSET" ] || die "No Python $PYTHON_VERSION build for $PY_TRIPLE in $PY_RELEASE."

PY_TARBALL="$CACHE/$(basename "$PY_ASSET")"
if [ ! -f "$PY_TARBALL" ]; then
  note "downloading $(basename "$PY_ASSET")"
  curl -fsSL -o "$PY_TARBALL.part" "$PY_ASSET"
  mv "$PY_TARBALL.part" "$PY_TARBALL"
else
  note "using the copy already in build/cache"
fi

# --- laying out the bundle ---------------------------------------------------

step "Building $APP_NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed -e "s|__BUNDLE_ID__|$BUNDLE_ID|g" \
    -e "s|__VERSION__|$VERSION|g" \
    -e "s|__BUILD__|$BUILD_NUMBER|g" \
    -e "s|__COPYRIGHT__|$COPYRIGHT|g" \
    "$ROOT/packaging/Info.plist.in" > "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null || die "Info.plist is not valid."
printf 'APPL????' > "$APP/Contents/PkgInfo"

note "compiling the launcher"
xcrun clang -O2 -Wall -Wextra -arch "$ARCH" \
  -mmacosx-version-min=11.0 \
  -o "$APP/Contents/MacOS/$EXECUTABLE" "$ROOT/packaging/launcher.c"

note "drawing the icon"
python3 "$ROOT/packaging/make_icon.py" "$APP/Contents/Resources/AppIcon.icns"

note "copying the tool"
tar -xzf "$PY_TARBALL" -C "$APP/Contents/Resources"
cp -R "$ROOT/app" "$APP/Contents/Resources/app"
cp "$ROOT/launch.py" "$APP/Contents/Resources/launch.py"
printf '%s\n' "$VERSION" > "$APP/Contents/Resources/VERSION"

# Trim the parts of Python a tool like this never touches.
PYLIB="$APP/Contents/Resources/python/lib/python${PYTHON_VERSION}"
rm -rf "$PYLIB/test" "$PYLIB/idlelib" "$PYLIB/tkinter" "$PYLIB/turtledemo" \
       "$PYLIB/lib2to3" "$PYLIB/ensurepip" "$PYLIB/distutils" \
       "$PYLIB/site-packages/pip"* "$PYLIB/site-packages/setuptools"* \
       "$APP/Contents/Resources/python/share" \
       "$APP/Contents/Resources/python/include" 2>/dev/null || true
rm -f "$PYLIB/lib-dynload/_tkinter"*.so 2>/dev/null || true
# tkinter has gone, so its Tcl/Tk runtime is dead weight - and every library
# left in the bundle is one more thing to sign and for Apple to check.
PYROOT="$APP/Contents/Resources/python"
for pattern in 'tcl*' 'tk*' 'itcl*' 'thread*' 'libtcl*' 'libtk*' 'libitcl*' \
               'libthread*' 'sqlite3*' 'pkgconfig'; do
  find "$PYROOT/lib" -maxdepth 1 -name "$pattern" -exec rm -rf {} + 2>/dev/null || true
done
find "$APP/Contents/Resources" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Resources" -name '*.pyc' -delete 2>/dev/null || true
find "$APP/Contents/Resources/python" -name '*.a' -delete 2>/dev/null || true

# The process that actually runs is this copy of the interpreter, so give it the
# application's name: that is what shows up in Activity Monitor.
REAL_PY=$(ls "$APP/Contents/Resources/python/bin/python${PYTHON_VERSION}" 2>/dev/null || true)
[ -n "$REAL_PY" ] || die "The bundled Python is not where it was expected."
cp "$REAL_PY" "$APP/Contents/Resources/python/bin/$EXECUTABLE"
chmod +x "$APP/Contents/Resources/python/bin/$EXECUTABLE"

note "bundle size: $(du -sh "$APP" | cut -f1)"

# --- a quick check that it actually runs -------------------------------------

step "Checking the bundled Python works"
PYTHONHOME="$APP/Contents/Resources/python" \
  "$APP/Contents/Resources/python/bin/$EXECUTABLE" -s -B -c \
  'import ssl, json, urllib.request, http.server, hashlib; print("    interpreter and ssl are fine")'

# --- signing -----------------------------------------------------------------

if [ "$DO_SIGN" -eq 1 ]; then
  step "Signing with the hardened runtime"
  ENTITLEMENTS="$ROOT/packaging/entitlements.plist"

  sign_one() {
    codesign --force --timestamp --options runtime \
             --sign "$SIGN_IDENTITY" "$@" >/dev/null
  }

  # Everything nested gets signed before the thing that contains it.
  note "signing the bundled interpreter and its modules"
  while IFS= read -r -d '' f; do
    case "$(file -b "$f")" in
      *Mach-O*) sign_one "$f" ;;
    esac
  done < <(find "$APP/Contents/Resources/python" -type f -print0)

  # The interpreter is the process that ends up running, so the entitlements
  # have to be on it, not only on the launcher.
  note "signing the interpreter with entitlements"
  codesign --force --timestamp --options runtime \
           --entitlements "$ENTITLEMENTS" \
           --sign "$SIGN_IDENTITY" \
           "$APP/Contents/Resources/python/bin/$EXECUTABLE" >/dev/null

  note "signing the launcher and the bundle"
  codesign --force --timestamp --options runtime \
           --entitlements "$ENTITLEMENTS" \
           --sign "$SIGN_IDENTITY" "$APP/Contents/MacOS/$EXECUTABLE" >/dev/null
  codesign --force --timestamp --options runtime \
           --entitlements "$ENTITLEMENTS" \
           --sign "$SIGN_IDENTITY" "$APP" >/dev/null

  note "verifying"
  codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | sed 's/^/    /'
fi

# --- notarisation ------------------------------------------------------------

notary_args() {
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    echo "--keychain-profile ${NOTARY_PROFILE}"
  elif [ -n "${NOTARY_KEY_ID:-}" ] && [ -n "${NOTARY_KEY_PATH:-}" ] \
       && [ -n "${NOTARY_ISSUER:-}" ]; then
    echo "--key ${NOTARY_KEY_PATH} --key-id ${NOTARY_KEY_ID} --issuer ${NOTARY_ISSUER}"
  elif [ -n "${NOTARY_APPLE_ID:-}" ] && [ -n "${NOTARY_TEAM_ID:-}" ] \
       && [ -n "${NOTARY_PASSWORD:-}" ]; then
    echo "--apple-id ${NOTARY_APPLE_ID} --team-id ${NOTARY_TEAM_ID} --password ${NOTARY_PASSWORD}"
  else
    echo ""
  fi
}

if [ "$DO_NOTARIZE" -eq 1 ]; then
  NARGS="$(notary_args)"
  [ -n "$NARGS" ] || die "No notarisation credentials. See BUILD.md; set NOTARY_PROFILE."

  step "Notarising the application"
  ZIP="$BUILD/$EXECUTABLE-app.zip"
  rm -f "$ZIP"
  ditto -c -k --keepParent "$APP" "$ZIP"
  # shellcheck disable=SC2086
  xcrun notarytool submit "$ZIP" $NARGS --wait 2>&1 | sed 's/^/    /'
  note "stapling the ticket to the app"
  xcrun stapler staple "$APP" 2>&1 | sed 's/^/    /'
  rm -f "$ZIP"
fi

# --- the disk image ----------------------------------------------------------

step "Packaging the disk image"
DMG="$DIST/$APP_NAME $VERSION.dmg"
STAGE="$BUILD/dmg"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/Read me first.txt" <<TXT
Authoring Assistant

1. Drag the Authoring Assistant icon onto the Applications folder shown here.
2. Open your Applications folder and double-click Authoring Assistant.
3. Your web browser opens, and that is where you use it.

That is all there is to it.
TXT

hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

if [ "$DO_SIGN" -eq 1 ]; then
  note "signing the disk image"
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG" >/dev/null
fi

if [ "$DO_NOTARIZE" -eq 1 ]; then
  note "notarising the disk image"
  NARGS="$(notary_args)"
  # shellcheck disable=SC2086
  xcrun notarytool submit "$DMG" $NARGS --wait 2>&1 | sed 's/^/    /'
  xcrun stapler staple "$DMG" 2>&1 | sed 's/^/    /'

  step "Final check, exactly as the author's Mac will see it"
  spctl -a -t open --context context:primary-signature -vv "$DMG" 2>&1 | sed 's/^/    /'
  spctl -a -t exec -vv "$APP" 2>&1 | sed 's/^/    /'
fi

step "Done"
note "app: $APP"
note "dmg: $DMG"
if [ "$DO_NOTARIZE" -eq 0 ]; then
  printf '\n    \033[33mNote: this build was not notarised. It will run on this Mac,\n'
  printf '    but Gatekeeper will refuse it on the author'"'"'s Mac.\033[0m\n\n'
fi
