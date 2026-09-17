# Building Authoring Assistant

Developer documentation. The author never reads this and never runs any of it —
they get a signed, notarised `.dmg`.

---

## What the build produces

A self-contained `Authoring Assistant.app` with its own copy of Python **and its
own copy of pandoc** inside it. The author's Mac needs nothing installed: no
Python, no Homebrew, no pandoc, no Command Line Tools.

- `build/Authoring Assistant.app` — the signed, stapled application
- `dist/Authoring Assistant <version>.dmg` — signed, notarised, stapled

Roughly 255 MB as an app, 70 MB as a disk image. pandoc accounts for almost all
of that: it is a 190 MB statically linked Haskell binary and there is no smaller
build of it. `--no-pandoc` produces the old ~65 MB app, at the cost of making the
author install pandoc themselves the first time they bring in a Word document.

## Requirements on the build machine

| Need | Why |
|---|---|
| Xcode command line tools | `clang` compiles the launcher; `codesign`, `notarytool`, `stapler` |
| A Developer ID Application certificate | signing |
| An App Store Connect key or app-specific password | notarisation |
| Network access | downloads the relocatable Python and pandoc at build time |
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
./packaging/build.sh --no-pandoc     # leave pandoc out; see "Word documents"
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
| `PANDOC_VERSION` | the latest release | e.g. `3.11`; pin it to keep builds reproducible |
| `REGISTRY_URL` | the registry's `registry.json` on `main` | the list of textbooks bundled as the offline fallback; see "Which book" below |
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
      pandoc/bin/pandoc            pandoc, for reading Word documents
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

### Word documents, and why pandoc is bundled

`app/convert.py` turns a `.docx` into a chapter. It shells out to pandoc with a
fixed set of flags:

```
-f docx -t gfm --wrap=none --extract-media=aa-extracted-media
```

**`--wrap=none` is not a preference.** Without it pandoc breaks every paragraph
at some column width, and the entire tool falls over: `edits.py` replaces a
stretch of characters on a single line and copies every other line through byte
for byte, and the vault's line-by-line history is only readable because a changed
sentence appears as one changed line. Wrapped output would make a one-word fix
look like a rewritten paragraph in every diff, for ever. The reason is written
beside the flag in `convert.py`, and `tests/test_all.py` fails if either the flag
or the explanation goes missing.

`-t gfm` is kept as it is, complex tables and all. Turning off `raw_html` would
produce prettier image syntax, but it would also silently flatten any table
pandoc could not express as a pipe table — losing the author's data rather than
merely making it ugly. Instead the app *reports* what came out; see
`convert.report`.

**Finding pandoc.** `convert.find_pandoc()` looks in the bundle first, then on
`PATH`, then in `LIKELY_PANDOC` — `/usr/local/bin`, Homebrew, MacPorts. That last
list matters: the app is launched from Finder, so it inherits almost no `PATH`
and `shutil.which` alone would miss a pandoc the author already has. The bundled
copy wins, so the app always uses the version it was tested against.

**When it is missing.** Only possible from a checkout, or a `--no-pandoc` build.
The app then offers a guided install: it downloads pandoc's own `.pkg` from
pandoc's own release page, refuses it unless `pkgutil --check-signature` reports
a Developer ID Installer certificate, and hands it to `/usr/bin/open` so Apple's
installer takes over. The package installs to `/usr/local/bin/pandoc`, which is
the first entry in `LIKELY_PANDOC`, so pressing "Check again" finds it at once.

The author is never told to run `brew`, or to open a terminal, anywhere in this
path. A test asserts that.

**Signing.** pandoc arrives signed by John MacFarlane, which counts for nothing
once it is inside our bundle — everything in an app must be signed by the
identity that signs the app. `build.sh` re-signs it with the hardened runtime and
**no entitlements**: it reads a file and writes a file, and never loads a library
of ours. The build also round-trips a real `.docx` through the bundled binary
before signing, so a broken or wrong-architecture copy is caught on the build
machine rather than on the author's Mac.

**Nothing is written until the author says so.** `convert.convert()` works in a
`tempfile.mkdtemp()` staging folder; `convert.save()` is the only thing that
touches the vault. It refuses to overwrite an existing chapter or a picture
folder that already has something in it — there is no undo in this tool — and
writes the pictures before the chapter, so a chapter never exists in the vault
pointing at pictures that failed to copy. If the chapter then fails to write, only
a picture folder the tool created itself is removed again.

**Where the pictures go.** Into the vault's `assets/<chapter>/`, not beside the
chapter. Three things outside this repository depend on that: the vault's
`docs/editing-the-textbook.md` tells authors to keep a chapter's images in
`assets/<chapter>/`; the CMS's `admin/config.yml` sets `media_folder: assets`;
and `docs/for-course-coordinators.md` tells a department building its own edition
to copy `chapters` and `assets` and nothing else — so a picture kept anywhere
else is silently missing from every edition. One folder per chapter is load-
bearing too: Word names the pictures inside every document `image1.png`,
`image2.png`, and two chapters sharing a folder would overwrite each other.

The author chooses the folder the chapter goes in, not the vault, so
`convert.find_vault_root()` walks up from that folder looking for the three
things that make a folder the top of this textbook — `chapters/`, `assets/` and
`glossary.md`. If it does not find them the import stops with
`convert.vault_problem()` rather than guessing; a plausible wrong answer here is
exactly the failure this arrangement exists to prevent.

**The rewrites.** pandoc extracts a picture to `aa-extracted-media/media/x.png`,
keeping the path it had inside the `.docx`, and writes every link to match.
`_collect_media` lifts the inner `media/` level away when the extracted tree is
exactly that shape (anything else is left as pandoc arranged it), and then
replaces the staging prefix throughout the text with the path from the chapter to
its folder under `assets` — `../assets/chapter-05`, percent-encoded, because a
chapter may be called `Chapter 6 (final)`. The link is relative to the chapter
rather than rooted at the vault because it has to resolve in three places at
once: Obsidian, the published site, and a department edition where `chapters` and
`assets` sit inside a `content` folder. Both rewrites happen before the author
sees the preview, so the links they read are the links that get written. This
breaks no promise: the untouched-lines rule is about chapters that already exist,
and this file does not yet.

---

## One-time sign-in setup (must be done by hand)

The console half signs the author in to GitHub **as themselves**, using the OAuth
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
| Application name | `Textbook Author Console` — the author sees this name when approving, and again in their list of authorised apps, so it must be recognisable |
| Homepage URL | `https://confused4now.org` |
| Authorization callback URL | `https://confused4now.org` (required by the form; device flow never uses it) |
| Enable Device Flow | **ticked** — without this, sign-in fails with `incorrect_client_credentials` |

Then copy the **Client ID**. It is not a secret; it identifies the application to
the sign-in page and nothing more. Do **not** generate a client secret — the app
neither needs one nor has anywhere safe to keep one.

### Giving it to the author

Nothing to do: the Client ID is `platform.console_oauth_client_id` in the
registry, which the app reads at launch. A value pasted in **Settings** →
**Signing in to see what is waiting** (stored in `state.json`) still takes
precedence, for testing a different OAuth App.

If neither exists — no registry copy at all and nothing pasted — the console
shows a plain "the technical contact needs to set this up" screen rather than
failing.

## Which book (the registry)

No book's repository, branches or site is written into the app. They come from
`textbook-registry/registry.json` (see `platform-registry-design/DESIGN.md` §3d):

- **Fetched once per launch** from the registry's `main`
  (`app/registry.py`, `REGISTRY_URL`; `AA_REGISTRY_URL` overrides it for
  development). A good copy is saved to `registry.json` in Application Support.
  If the fetch fails, the saved copy is used, then the copy the build bundled
  (`app/registry.bundled.json`, fetched by `build.sh`, never committed). The page
  says "List of textbooks as of …" whenever the copy isn't fresh. A list that
  arrives broken never replaces a good saved one.
- **The list is checked again on arrival**: duplicate keys, slugs or repos, an
  unknown `schema_version`, or drafts branch = live branch all refuse the whole
  list. A retired or unknown slug never resolves, and nothing falls back to
  another book.
- **Book picker.** `GET /repos/{repo}` per non-retired book, with the author's own
  token. Only books where `permissions` has push, maintain or admin are offered
  (a private repo is never offered, since `public_repo` can't reach it). The
  answer is saved per account in `state.json` for offline use, and cleared on
  sign-out. The last choice is remembered as `last_book`.
- **The vault decides.** One vault is open for the whole app, whichever half
  opened it. Opening it reads `textbook.config.json`'s `slug` and the
  `origin` remote in `.git/config` (worktrees followed; host ignored, since the
  maintainer uses an SSH alias), and both must match one registered book, or
  the vault is refused. An open vault sets the book and locks the picker.
- **The write is what enforces it.** Before a suggestion is written into a
  chapter, `_guard_book_write` re-reads the vault's identity from disk and
  refuses unless it is unchanged since opening, is still the book the suggestion
  was fetched from, and is the vault the change was planned in. The Chapters
  tools' saves go through `_guard_vault_write` (the target is inside the open
  vault, and the vault's claim is unchanged and still holds). A suggestion's page
  path is resolved with links and `..` followed, and refused if it leaves the
  vault.
- **Every console request names its book.** The page sends the slug it was drawn
  for, and the server refuses any request whose slug isn't the current book, so
  a stale tab can't act on the wrong one. The suggestion text used for a plan is
  the one the server fetched for that book, not what the page sends.

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

**It does not publish by itself.** Accepting a proposed change folds it into
`drafts` and then opens — or refreshes — the single pull request from `drafts`
into `main`, so the change is on its way to readers rather than stranded. It
stops one press short of merging that request, for three reasons. `drafts` is
shared: it carries whatever has been written in the Sveltia CMS as well as
whatever the console has accepted, so merging on the strength of one press about
one change would publish other people's unreviewed work. `main` is protected and
has checks of its own, which a desktop app is in no position to wait for. And the
author's vault tracks `main`, so writing there behind the author's back would
leave that copy silently out of date and set up the next conflict. Publishing
is therefore its own screen, showing what would go, with a box to tick — and it
says afterwards that the vault now needs pulling.

There is exactly one publish request at a time and its description is **rewritten**
on each accept, never appended to, because what it carries is `drafts` as it
stands and not the change just accepted. It merges with `merge_method: "merge"`,
not `squash`: `drafts` is long-lived, so it has to stay an ancestor of `main`, or
every accept would offer the whole of its history again. Whether it can be merged
is read from the service and reported as `clean`, `conflict`, `blocked` or
`unknown` — never guessed, and `unknown` is shown to the author as unknown. See
"accepting reaches the live book" in `tests/test_all.py`.

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
python3 -m tests.test_all     # 279 checks: the analyses, the file-safety promises,
                              #             the Word conversion, the console's
                              #             refusal rules, the path from accepting
                              #             a change to the live book, the
                              #             registry, the book picker, the vault
                              #             and book mismatch refusals, and the
                              #             words the troubleshooting guide quotes
node tests/ui_flow.js         # 79 checks: the review, import, book-choosing and
                              #            going-live flows, driven against the
                              #            real app.js
```

The Python suite covers the things that must never break: that untouched lines
stay byte-for-byte identical, that a second run finds nothing to do, that a file
changed on disk is refused, that the glossary merges alphabetically without
duplicates, and that the conversion keeps `--wrap=none`, never overwrites, and
reports what the author has to check.

The conversion checks that need a real `.docx` build one with pandoc and skip
themselves on a machine that has none; the report checks are driven from text, so
they always run.

To run the suite against the bundled interpreter rather than the system one:

```sh
APP="build/Authoring Assistant.app/Contents/Resources"
PYTHONHOME="$APP/python" "$APP/python/bin/AuthoringAssistant" -m tests.test_all
```

## Releasing

1. Bump `VERSION`.
2. `export NOTARY_PROFILE=authoring-assistant && ./packaging/build.sh`
3. Check the build said a Word document converted correctly.
4. Check the final `spctl` output says `accepted` and `Notarized Developer ID`.
5. Send the author the `.dmg`. Nothing else.

Notarising a 255 MB app takes noticeably longer than it used to. The pandoc
download is cached in `build/cache`, so only the first build of a given version
pays for it.

## Never commit

`.gitignore` already covers these, but to be explicit: no API keys, no
`build/`, no `dist/`, and no certificates. The author's DeepSeek key lives only
in `~/Library/Application Support/Authoring Assistant/deepseek.key`, mode `600`.
