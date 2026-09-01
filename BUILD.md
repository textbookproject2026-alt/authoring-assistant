# Building Authoring Assistant

Developer documentation. The author never reads this and never runs any of it —
they get a signed, notarised `.dmg`.

---

## What the build produces

A self-contained `Authoring Assistant.app` with its own copy of Python inside it.
The author's Mac needs nothing installed: no Python, no Homebrew, no pandoc, no
Command Line Tools.

- `build/Authoring Assistant.app` — the signed, stapled application
- `dist/Authoring Assistant <version>.dmg` — signed, notarised, stapled

Roughly 65 MB as an app, 28 MB as a disk image.

## Requirements on the build machine

| Need | Why |
|---|---|
| Xcode command line tools | `clang` compiles the launcher; `codesign`, `notarytool`, `stapler` |
| A Developer ID Application certificate | signing |
| An App Store Connect key or app-specific password | notarisation |
| Network access | downloads the relocatable Python at build time |
| Any Python 3 | only to draw the icon during the build |

## One-time notarisation setup

Store the credentials in the keychain so they never appear in a script or a
shell history:

```sh
xcrun notarytool store-credentials "authoring-assistant" \
  --apple-id "you@example.com" \
  --team-id "YOURTEAMID" \
  --password "abcd-efgh-ijkl-mnop"      # an app-specific password
```

## Building

```sh
export NOTARY_PROFILE="authoring-assistant"
./packaging/build.sh
```

That is the whole thing. It will: fetch a relocatable CPython, compile the
launcher, draw the icon, assemble the bundle, sign everything inside it with the
hardened runtime, notarise the app, staple the ticket, build the disk image,
sign and notarise that too, staple it, and finally check the result exactly as
Gatekeeper will on the author's Mac.

### Options

```sh
./packaging/build.sh --no-notarize   # build and sign only — fast local check
./packaging/build.sh --no-sign       # build only — no certificate needed
```

### Settings

All read from the environment; nothing is hardcoded.

| Variable | Default | Notes |
|---|---|---|
| `SIGN_IDENTITY` | asked for, or the only Developer ID in the keychain | never hardcoded |
| `BUNDLE_ID` | `com.alecgordon.authoring-assistant` | |
| `VERSION` | `1.0.0` | shown in the app's Settings panel |
| `BUILD_NUMBER` | a timestamp | `CFBundleVersion` |
| `PYTHON_VERSION` | `3.12` | the interpreter bundled |
| `ARCH` | this machine's | `arm64` or `x86_64` |
| `NOTARY_PROFILE` | — | keychain profile, the easiest option |
| `NOTARY_APPLE_ID` / `NOTARY_TEAM_ID` / `NOTARY_PASSWORD` | — | alternative to the profile |
| `NOTARY_KEY_ID` / `NOTARY_KEY_PATH` / `NOTARY_ISSUER` | — | App Store Connect API key |

If `SIGN_IDENTITY` is not set and exactly one Developer ID Application
certificate is installed, the script uses it and says so. With more than one it
asks which.

### Intel Macs

`python-build-standalone` does not publish universal binaries, so one build
covers one architecture. For an Intel build, run `ARCH=x86_64 ./packaging/build.sh`
on any Mac and ship the two disk images separately.

---

## How the application is put together

```
Authoring Assistant.app/
  Contents/
    Info.plist                     LSUIElement — no Dock icon, no menu bar, no window
    MacOS/AuthoringAssistant       the launcher (compiled from packaging/launcher.c)
    Resources/
      launch.py                    puts Resources on the import path, calls the server
      app/                         the tool itself
      python/                      a complete relocatable CPython
        bin/AuthoringAssistant     the interpreter, renamed so Activity Monitor reads well
      AppIcon.icns                 drawn by packaging/make_icon.py
      VERSION
```

### Why the launcher exits immediately

`packaging/launcher.c` forks, the parent returns at once, and the child calls
`setsid()` and `execv`s the bundled interpreter.

This matters. If the launcher stayed alive, macOS would consider the application
"open", and a second double-click would try to activate it — sending an Apple
Event that a plain Python process has no way to answer, so nothing would happen
and the author would think the app was broken. Because the launcher exits, every
double-click runs it afresh; it finds the server already running, reopens the
page in the browser, and exits again.

`LSUIElement` keeps it out of the Dock and off the menu bar. Nothing is ever
drawn on screen by the app itself — the browser page is the entire interface.
Standard output and standard error go to
`~/Library/Application Support/Authoring Assistant/log.txt`, which is the first
place to look if anything misbehaves.

### Signing, and the one subtlety

The process that actually runs is `Resources/python/bin/AuthoringAssistant`, not
`Contents/MacOS/AuthoringAssistant`, because the launcher `execv`s it. Entitlements
are granted per-executable, so **the entitlement has to be on the interpreter**,
not only on the app bundle. The build script signs it explicitly for that reason.
Getting this wrong produces an app that runs unsigned and fails the moment the
hardened runtime is enabled.

The only entitlement used is
`com.apple.security.cs.disable-library-validation`, which an interpreter needs in
order to load its own extension modules at run time. There is no sandbox, so no
network entitlement is required for the DeepSeek call.

Everything is signed innermost-first: every Mach-O file under `Resources/python`,
then the interpreter with entitlements, then the launcher, then the bundle.

### How it shuts down

There is no menu bar item to quit from, so the server decides for itself:

- the page sends `/api/ping` every 5 seconds;
- closing the tab or quitting the browser fires `navigator.sendBeacon` to
  `/api/bye`, and the server stops 12 seconds later — long enough that a page
  reload does not kill it;
- if pings stop without a goodbye (a crashed browser, a sleeping Mac) the server
  gives up after 60 seconds;
- if the page never appears at all, it gives up after 4 minutes;
- **Quit** in the page's top corner stops it at once.

`~/Library/Application Support/Authoring Assistant/runtime.json` records the port
and token of the running copy, so a second launch reconnects instead of starting
a rival. It is removed on exit, and a stale one is detected and ignored.

---

## One-time sign-in setup (must be done by hand)

The console half signs the author in to GitHub **as himself**, using the OAuth
**device flow**. Nothing about this ships as a secret, and the app hosts no
callback address, so there is nothing to deploy and nothing to keep running.

### Why not the CMS's OAuth app and relay

The Sveltia relay (`sveltia-cms-auth`) exists for a browser page: it hands the
token back by `postMessage` to the window that opened it, and it gates on
`ALLOWED_DOMAINS`, currently `textbook-cms.pages.dev`. This app serves on
`http://127.0.0.1:<random port>` — not a fixed origin, and not on that list.
Making it fit would mean widening `ALLOWED_DOMAINS` to include loopback, which
weakens the CMS's own protection, and pinning this app to a fixed port. It would
also couple the author's console to a Worker whose real job is the CMS: one
misconfiguration would break both.

Device flow avoids all of it. GitHub OAuth apps have no public-client/PKCE mode,
so a desktop app must otherwise either ship a client secret (extractable from the
`.app`) or depend on a relay. Device flow needs neither.

### What to create

Create a **new** OAuth app — do not add a second callback URL to "Textbook CMS".
Device flow needs no callback at all, and sharing one app would mean revoking the
console also revokes the CMS.

At <https://github.com/settings/developers> → **New OAuth App**:

| Field | Value |
|---|---|
| Application name | `Textbook Author Console` — the author sees this name when approving, and again in his authorised-apps list, so it must be recognisable |
| Homepage URL | `https://bptext2026.xyz` |
| Authorization callback URL | `https://bptext2026.xyz` (required by the form; device flow never uses it) |
| Enable Device Flow | **ticked** — without this, sign-in fails with `incorrect_client_credentials` |

Then copy the **Client ID**. It is not a secret; it identifies the application to
the sign-in page and nothing more. Do **not** generate a client secret — the app
neither needs one nor has anywhere safe to keep one.

### Giving it to the author

Open the app → **Settings** → **Signing in to see what is waiting** → paste the
Client ID → **Save identifier**. It is stored in `state.json` under Application
Support (not a secret, so not in the Keychain). Done once per Mac.

Until it is set, the console shows a plain "Alec needs to set this up" screen
rather than failing.

### The scope, and why it is narrow

`app/github.py` requests **`public_repo`**, not `repo`. The textbook repo is
public, and `public_repo` still allows closing a suggestion, replying, and
accepting a draft change — while giving no access whatsoever to any private
repository the author owns. Do not widen this without a reason.

### Revoking

The author can withdraw access at any time at
<https://github.com/settings/applications>. The app handles a revoked token the
same way it handles an expired one: it says the sign-in is no longer accepted and
offers to sign in again. It never fails silently.

## Where the secrets live

Both secrets are in the **login Keychain**, under the service
`Authoring Assistant`, told apart by account name (`github-token`,
`deepseek-key`). Neither is in a file, and neither is ever in the vault.

`app/keychain.py` shells out to `/usr/bin/security` and writes the secret on
**standard input**, never as a command-line argument, so it never appears in the
process list. Every save is verified by reading it back before being reported as
saved.

A DeepSeek key left over from an older version, in
`~/Library/Application Support/Authoring Assistant/deepseek.key`, is migrated to
the Keychain the first time it is read, and the file is removed only once the
Keychain has it.

**Expect a Keychain prompt after a re-signed build.** A Keychain item's access
control binds to the signing identity, so the first run of a new build may ask
whether Authoring Assistant may use the Keychain. It is worth warning the author
before a release; the README already says to say yes.

## What the console deliberately does not do

Two things were specified and left out on purpose. Both are recorded here so the
decision is not silently reversed later.

**It does not apply reader suggestions to chapters by guessing.** A suggestion is
free prose with no target and no replacement text. The console applies a change
itself only when the suggestion contains an exact quoted replacement AND the old
wording appears in the chapter exactly once — verified against the file on disk,
and written through the same `apply_edits` path as the analyses, so the
untouched-lines guarantee holds. Everything else is handed to the author with the
suggestion pinned beside it. See the tests under "The console" in
`tests/test_all.py`.

**It does not auto-merge the weekly generated pull requests.** The three
generator workflows (`contributors.yml`, `derivatives.yml`, `dashboard.yml`) open
PRs into protected `main`. Merging them from a desktop app would make the merge
contingent on when the author happens to open a Mac app, would write to `main`
with no preview, and — if matched on branch name alone — would merge anything
pushed to a branch called `chore/*-update`. That belongs on GitHub: either
`gh pr merge --auto` inside each workflow, or letting those jobs write to their
own branch as `backup-annotations.yml` already does. **Still to be decided.**

## Tests

```sh
python3 -m tests.test_all     # 86 checks: the analyses, the file-safety promises,
                              #            and the console's refusal rules
node tests/ui_flow.js         # 27 checks: the review flow, driven against real app.js
```

The Python suite covers the things that must never break: that untouched lines
stay byte-for-byte identical, that a second run finds nothing to do, that a file
changed on disk is refused, and that the glossary merges alphabetically without
duplicates.

To run the suite against the bundled interpreter rather than the system one:

```sh
APP="build/Authoring Assistant.app/Contents/Resources"
PYTHONHOME="$APP/python" "$APP/python/bin/AuthoringAssistant" -m tests.test_all
```

## Releasing

1. Bump `VERSION`.
2. `export NOTARY_PROFILE=authoring-assistant && ./packaging/build.sh`
3. Check the final `spctl` output says `accepted` and `Notarized Developer ID`.
4. Send the author the `.dmg`. Nothing else.

## Never commit

`.gitignore` already covers these, but to be explicit: no API keys, no
`build/`, no `dist/`, and no certificates. The author's DeepSeek key lives only
in `~/Library/Application Support/Authoring Assistant/deepseek.key`, mode `600`.
