"""
Bringing a Word document into the vault.

This replaces a step the author could not carry out themselves: turning a .docx
into a markdown chapter used to mean a pandoc command line. The conversion is the
same one; what is new is that the file is chosen with the file picker, the result
is shown before it is written, and what came out badly is explained in words
rather than left to be discovered later.

Four rules shape everything here.

  * Nothing is written into the vault until the author has seen the whole
    converted chapter and said yes. Conversion happens in a temporary folder.
  * We never overwrite. If a chapter of that name already exists we stop and ask
    for another name, because this tool has no undo.
  * The conversion flags are fixed, not options. See PANDOC_ARGS below.
  * The pictures go into the vault's own `assets` folder, one folder per
    chapter - not beside the chapter. See "where the pictures go" below for the
    three things that depend on that and cannot see this code.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import config

# --- how we call pandoc ------------------------------------------------------
#
# -t gfm        GitHub-flavoured markdown: the dialect Obsidian reads, with
#               pipe tables and [^1] footnotes.
#
# --wrap=none   MANDATORY. Without it pandoc breaks every paragraph across
#               several lines at some column width. The whole tool is built on
#               one paragraph per line: edits.py replaces a stretch of
#               characters on a single line and copies every other line through
#               byte for byte, and the vault's line-by-line history is only
#               readable because a changed sentence shows up as one changed
#               line. Wrapped output would make a one-word fix look like a
#               rewritten paragraph in every diff, for ever. Do not remove this,
#               and do not make it an option the author can turn off.
#
# --extract-media  pulls the pictures out of the .docx instead of leaving them
#               locked inside the Word file. pandoc puts them in a folder beside
#               the chapter and writes the links to match; neither is where they
#               belong, so both are redirected into the vault's `assets` folder
#               afterwards. See "where the pictures go" below.
#
PANDOC_ARGS = ["-f", "docx", "-t", "gfm", "--wrap=none"]

CONVERT_TIMEOUT = 300           # a long chapter with many pictures
DOWNLOAD_TIMEOUT = 900          # the installer is a large download

# Where a copy of pandoc might already be. The app is started from Finder, so it
# inherits almost no PATH at all - /usr/local/bin and Homebrew are not on it.
# Looking in the real places by hand is the only way to find a copy the author
# already has.
LIKELY_PANDOC = [
    "/usr/local/bin/pandoc",        # where pandoc's own installer puts it
    "/opt/homebrew/bin/pandoc",
    "/usr/bin/pandoc",
    "/opt/local/bin/pandoc",        # MacPorts
    os.path.expanduser("~/.local/bin/pandoc"),
]

# The download offered if there is no pandoc at all. It is pandoc's own signed
# installer package, from pandoc's own release page: the author double-clicks it
# and Apple's installer does the rest. There is deliberately no instruction here
# to type anything into a terminal.
# The package installs a single executable at /usr/local/bin/pandoc, which is
# the first place LIKELY_PANDOC looks, so a finished install is found at once.
PANDOC_RELEASES_API = "https://api.github.com/repos/jgm/pandoc/releases/latest"
PANDOC_DOWNLOAD_PAGE = "https://github.com/jgm/pandoc/releases/latest"
PANDOC_DOWNLOAD_HOST = "github.com"
# Used only when this Mac cannot reach the release list. A known-good version is
# far better than a dead end.
PANDOC_FALLBACK_VERSION = "3.11"


# --- finding pandoc ----------------------------------------------------------

def bundled_pandoc():
    """The copy inside the .app, if this build carries one."""
    here = os.path.dirname(os.path.abspath(__file__))
    resources = os.path.dirname(here)
    candidate = os.path.join(resources, "pandoc", "bin", "pandoc")
    return candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None


def find_pandoc():
    """Return (path, where) for the pandoc we will use, or (None, None).

    The bundled copy wins, so an author who also has an old pandoc installed for
    something else still gets the version this tool was tested against.
    """
    inside = bundled_pandoc()
    if inside:
        return inside, "bundled"

    found = shutil.which("pandoc")
    if found:
        return found, "installed"

    for candidate in LIKELY_PANDOC:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate, "installed"
    return None, None


def pandoc_version(path):
    try:
        proc = subprocess.run([path, "--version"], capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or "").strip().split("\n")[0]
    m = re.search(r"(\d+\.\d+(\.\d+)*)", first)
    return m.group(1) if m else first or None


def status():
    """What the interface needs to decide which screen to draw."""
    path, where = find_pandoc()
    if not path:
        return {
            "ready": False,
            "where": None,
            "version": None,
            "can_install": True,
        }
    return {
        "ready": True,
        "where": where,
        "version": pandoc_version(path),
        "can_install": False,
    }


# --- the guided install ------------------------------------------------------

def _installer_url():
    """The right installer for this Mac, from pandoc's own release page."""
    import platform
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    want = re.compile(r"^https://github\.com/jgm/pandoc/releases/download/"
                      r"[^/]+/pandoc-[^/]+-" + arch + r"-macOS\.pkg$")
    try:
        req = urllib.request.Request(
            PANDOC_RELEASES_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "Authoring Assistant"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
        for url in re.findall(r'"browser_download_url":\s*"([^"]+)"', body):
            if want.match(url):
                return url, None
    except (urllib.error.URLError, OSError, ValueError, UnicodeDecodeError):
        pass

    guess = (f"https://github.com/jgm/pandoc/releases/download/"
             f"{PANDOC_FALLBACK_VERSION}/pandoc-{PANDOC_FALLBACK_VERSION}"
             f"-{arch}-macOS.pkg")
    return guess, "the release list could not be read, so a known good version "\
                  "was used instead"


def _properly_signed(pkg_path):
    """True if the downloaded package is signed by a known Apple developer.

    We are about to hand this file to Apple's installer, which will ask the
    author for their password. Checking the signature first means an
    interrupted or interfered-with download is refused rather than opened.
    """
    try:
        proc = subprocess.run(["/usr/sbin/pkgutil", "--check-signature", pkg_path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 and "Developer ID Installer" in out


def download_installer():
    """Fetch pandoc's own installer and hand it to Apple's installer app.

    Returns (ok, message). The author never types anything; they answer the
    installer's own questions and come back.
    """
    url, caveat = _installer_url()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != PANDOC_DOWNLOAD_HOST:
        return False, ("The download address was not one we recognise, so nothing "
                       "was downloaded.")

    folder = config.ensure_dir()
    target = os.path.join(folder, os.path.basename(url))
    partial = target + ".part"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Authoring Assistant"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, \
                open(partial, "wb") as fh:
            shutil.copyfileobj(resp, fh, 1024 * 512)
        os.replace(partial, target)
    except (urllib.error.URLError, OSError, ValueError) as e:
        try:
            os.remove(partial)
        except OSError:
            pass
        return False, ("The download did not finish, so nothing was installed. "
                       "This Mac may not be online. The details were: " + str(e))

    if not _properly_signed(target):
        try:
            os.remove(target)
        except OSError:
            pass
        return False, ("The file that arrived was not signed by its publisher, so "
                       "it was thrown away rather than opened. Nothing was "
                       "installed and nothing was changed. Please try again.")

    try:
        subprocess.run(["/usr/bin/open", target], check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False, ("The installer was downloaded to " + target +
                       " but it could not be opened. You can open it yourself "
                       "from that folder in Finder.")

    message = ("An installer window has opened. Press Continue, then Install — "
               "it will ask for this Mac's password, which is normal for any "
               "installer. When it says it has finished, come back here and "
               "press “Check again”.")
    if caveat:
        message += " (Note: " + caveat + ".)"
    return True, message


# --- naming ------------------------------------------------------------------

def suggest_name(docx_path):
    """A sensible markdown file name from the Word file's own name."""
    base = os.path.basename(docx_path)
    # Not splitext: a file called ".docx" has no stem as far as it is concerned,
    # and would come back as ".docx" whole.
    if base.lower().endswith(".docx"):
        base = base[:-5]
    base = base.strip().strip(".")
    # Word file names routinely carry a trailing " (1)", " final", " v3" and so
    # on. We leave those alone: guessing what the author meant to call it would
    # be worse than showing them their own name and letting them edit it.
    base = re.sub(r'[/\\:*?"<>|]', "-", base)
    base = re.sub(r"\s+", " ", base).strip()
    return (base or "Untitled chapter") + ".md"


# --- the one chapter-naming rule ---------------------------------------------
#
# Every book names a chapter `chapters/chapter-NN.md`, with its pictures in
# `assets/chapter-NN/` (decided 27 Sep 2026). A live book keeps the names it
# already has: its addresses are live. So a Word file becomes the next free
# chapter-NN the first time, and the book remembers which chapter it became, in
# `chapter-sources.json` at the top of the book, so that bringing the same Word
# file in again replaces that same chapter. book-requests writes the same file
# when it makes a book from a manuscript, and follows the same rule.

SOURCES_FILE = "chapter-sources.json"
CHAPTER_RE = re.compile(r"chapter-(\d+)\.md")
SOURCES_NOTE = ("Which chapter each Word file became, so bringing the same file in "
                "again replaces the same chapter. Written by the Authoring "
                "Assistant and book-requests; keys are Word file names.")


def source_key(word_name):
    """A Word file's name as the book remembers it: the name alone, however it
    was typed or copied (Unicode, case and spacing don't count)."""
    base = os.path.basename(word_name or "")
    base = unicodedata.normalize("NFC", base)
    return re.sub(r"\s+", " ", base).strip().casefold()


def read_sources(root):
    """{source key: chapter path} from the book at `root`. {} when there's no
    such file, or it isn't the shape this writes (a broken file is never a
    reason to refuse an import: the chapter just isn't matched)."""
    try:
        with open(os.path.join(root, SOURCES_FILE), encoding="utf-8") as fh:
            return parse_sources(fh.read())
    except OSError:
        return {}


def parse_sources(text):
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, dict):
        return {}
    return {source_key(k): v for k, v in chapters.items()
            if isinstance(k, str) and isinstance(v, str)}


def sources_text(old_text, word_name, chapter_path):
    """The book's chapter-sources.json with this Word file recorded as
    `chapter_path`, keeping every other entry as it was. Returns the text."""
    try:
        data = json.loads(old_text) if old_text else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    chapters = data.get("chapters") if isinstance(data.get("chapters"), dict) else {}
    key = source_key(word_name)
    chapters = {k: v for k, v in chapters.items() if source_key(k) != key}
    chapters[os.path.basename(word_name)] = chapter_path
    out = {"_note": SOURCES_NOTE,
           "chapters": dict(sorted(chapters.items(), key=lambda kv: kv[1]))}
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _legacy_names(word_name):
    """The names a chapter from this Word file had before the rule: the app's
    old suggestion (the Word file's own name) and book-requests' slug of it."""
    stem = suggest_name(word_name)[:-3]
    slug = unicodedata.normalize("NFKD", stem.lower())
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:60] or "chapter"
    return [stem + ".md", slug + ".md"]


def chapter_name(word_name, existing, sources, folder_rel="chapters"):
    """(file name, how) for a Word file going into the book's chapters folder.

    `existing` holds the names of the files there now, `sources` is
    read_sources(). `how` says why, for the author:
      "recorded"  this Word file became that chapter last time
      "existing"  a chapter already has this Word file's pre-rule name
                  (a live book keeps its names)
      "new"       the next free chapter-NN
    """
    existing = set(existing)
    recorded = sources.get(source_key(word_name))
    if recorded and recorded.startswith(folder_rel + "/") and "/" not in recorded[len(folder_rel) + 1:]:
        return recorded[len(folder_rel) + 1:], "recorded"
    for name in _legacy_names(word_name):
        if name in existing:
            return name, "existing"
    taken = [int(m.group(1)) for n in existing | {os.path.basename(v) for v in sources.values()}
             for m in [CHAPTER_RE.fullmatch(n)] if m]
    return f"chapter-{max(taken, default=0) + 1:02d}.md", "new"


def rule_problem(md_name, existing):
    """Why a chapter can't have this name in the chapters folder, or None. A new
    chapter is chapter-NN.md; an existing file may be replaced whatever its name."""
    if md_name in set(existing) or CHAPTER_RE.fullmatch(md_name):
        return None
    return ("A new chapter is named chapter-NN.md, with the next free number "
            "(chapter-04.md, say): every book on the platform names its chapters "
            "that way. The suggested name already follows it.")


def _slug(md_name):
    return os.path.splitext(os.path.basename(md_name))[0]


# --- where the pictures go ---------------------------------------------------
#
# Into the vault's `assets` folder, in a folder named after the chapter:
# `chapters/chapter-05.md` puts its pictures in `assets/chapter-05/`. Three
# things outside this file depend on that, and none of them can see this code:
#
#   * `docs/editing-the-textbook.md` tells authors that a chapter's images live
#     in `assets/<chapter>/`, a fresh folder per chapter.
#   * `admin/config.yml` gives the website's editor `media_folder: assets`, so a
#     picture added through the website lands there too.
#   * `docs/for-course-coordinators.md` tells a department building its own
#     edition to copy `chapters` and `assets`, and nothing else. A picture kept
#     anywhere else is missing from every edition, and missing silently.
#
# One folder per chapter is not tidiness. Word names the pictures inside every
# document `image1.png`, `image2.png`, so two chapters sharing a folder would
# write over each other's figures.
#
# The author chooses the folder the chapter goes in, not the vault, so the vault
# has to be worked out by walking up from that folder until we find the three
# things that make a folder the top of this textbook. If we do not find them we
# stop and say so. Guessing would put the pictures somewhere plausible and
# wrong, which is the failure this exists to prevent.

MEDIA_ROOT = "assets"           # the folder in the vault all pictures live in
VAULT_SEARCH_DEPTH = 8          # how far up to look before giving up
STAGE_MEDIA = "aa-extracted-media"   # what pandoc extracts into, in /tmp only


def _is_vault_root(path):
    return (os.path.isdir(os.path.join(path, "chapters"))
            and os.path.isdir(os.path.join(path, MEDIA_ROOT))
            and os.path.isfile(os.path.join(path, "glossary.md")))


def find_vault_root(folder):
    """The top of the textbook, found by walking up from the chosen folder.

    None when the chosen folder is not inside one. Callers must not carry on
    with a guess - say so instead, with vault_problem().

    Not the same thing as session.find_vault_root(), which starts from a chapter
    the author already has, looks for Obsidian's own `.obsidian` folder, and
    falls back to the chapter's own folder rather than failing. That fallback is
    harmless there and would be the whole bug here.
    """
    if not folder:
        return None
    probe = os.path.abspath(folder)
    for _ in range(VAULT_SEARCH_DEPTH):
        if _is_vault_root(probe):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return None


def vault_problem(folder):
    """Why we could not find the textbook, in the author's words."""
    return (
        "That folder does not seem to be inside your textbook. A chapter's "
        "pictures are kept in the textbook's own “assets” folder, so this tool "
        "has to be able to find the top of the textbook — the folder holding "
        "“chapters”, “assets” and “glossary.md” - by looking upwards from where "
        "the chapter is going. Starting at “"
        + (os.path.basename(os.path.abspath(folder).rstrip(os.sep)) or folder)
        + "” it did not find one. Please choose a folder inside your textbook, "
        "such as its “chapters” folder."
    )


def media_destination(folder, md_name):
    """Where this chapter's pictures go, and how the chapter links to them.

    None when the folder is not inside a vault. Otherwise a dict:

      vault        the top of the textbook
      target       the folder the pictures are copied into
      rel          that folder as the author would name it: assets/chapter-05
      link_prefix  what goes in the chapter's picture links

    The link is relative to the chapter, not rooted at the vault, because it has
    to work in three places at once: Obsidian on the author's Mac, the published
    site, and a department edition where `chapters` and `assets` have been
    copied inside a `content` folder. A path from the chapter to the picture is
    the only one that survives all three. It is percent-encoded because a
    chapter may be called "Chapter 6 (final)" and a raw space or bracket would
    break the link.
    """
    vault = find_vault_root(folder)
    if not vault:
        return None
    stem = _slug(md_name)
    target = os.path.join(vault, MEDIA_ROOT, stem)
    rel = os.path.relpath(target, vault)
    from_chapter = os.path.relpath(target, os.path.abspath(folder))
    link = "/".join(urllib.parse.quote(part)
                    for part in from_chapter.split(os.sep))
    return {"vault": vault, "target": target,
            "rel": rel.replace(os.sep, "/"), "link_prefix": link}


# --- the conversion itself ---------------------------------------------------

class ConversionFailed(Exception):
    pass


def convert(docx_path, md_name, folder):
    """Convert into a temporary folder. Nothing in the vault is touched.

    `folder` is where in the vault the chapter is going. It is needed here and
    not only at save time because the picture links written into the text point
    into the vault's `assets` folder, and the author reads the whole converted
    chapter before agreeing to save it - so the links they read have to be the
    ones that will be written.

    Returns a dict holding the converted text, the staging folder, where the
    pictures are going, and the list of pictures that came out.
    """
    pandoc, _ = find_pandoc()
    if not pandoc:
        raise ConversionFailed(
            "The part that reads Word documents is not set up on this Mac yet."
        )
    if not os.path.isfile(docx_path):
        raise ConversionFailed("That Word document could not be found. Please "
                               "choose it again.")

    dest = media_destination(folder, md_name)
    if dest is None:
        raise ConversionFailed(vault_problem(folder))

    stage = tempfile.mkdtemp(prefix="aa-import-")
    out_name = "converted.md"

    # Extracted under a fixed name in the temporary folder, never under the name
    # it is going to have. The links are rewritten off it below.
    argv = [pandoc, os.path.abspath(docx_path)] + PANDOC_ARGS + [
        f"--extract-media={STAGE_MEDIA}", "-o", out_name,
    ]
    try:
        proc = subprocess.run(argv, cwd=stage, capture_output=True, text=True,
                              timeout=CONVERT_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(stage, ignore_errors=True)
        raise ConversionFailed(
            "Reading that Word document took too long and was stopped. Nothing "
            "was changed. Very long documents with a great many pictures "
            "sometimes do this; try splitting it into two."
        )
    except OSError as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise ConversionFailed("The converter could not be run: " + str(e))

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().split("\n")[-1] if proc.stderr else ""
        shutil.rmtree(stage, ignore_errors=True)
        raise ConversionFailed(
            "That file could not be read as a Word document, so nothing was "
            "changed. Make sure it is a .docx saved by Word — an older .doc, or "
            "a file that has been renamed, will not work."
            + (f" The converter said: {detail}" if detail else "")
        )

    out_path = os.path.join(stage, out_name)
    try:
        with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise ConversionFailed("The converted chapter could not be read back: "
                               + str(e))

    text, media = _collect_media(stage, text, dest["link_prefix"])
    text = keep_front_matter(os.path.join(folder, md_name), text)

    warnings = [w for w in (proc.stderr or "").strip().split("\n") if w.strip()]
    return {
        "stage": stage,
        "text": text,
        "media_rel": dest["rel"] if media else None,
        "media_target": dest["target"] if media else None,
        "media": media,
        "pandoc_warnings": warnings[:12],
        "docx": os.path.abspath(docx_path),
        "md_name": md_name,
    }


FRONT_MATTER = re.compile(r"\A---\r?\n.*?^---[ \t]*(\r?\n|\Z)", re.S | re.M)


def keep_front_matter(old_path, text):
    """A chapter that replaces one already at `old_path` keeps that chapter's
    front matter (topic and so on). Word has none, so without this every
    re-import would drop it. Front matter the new text brings itself wins.
    A `title:` that says what the new chapter's first `# ` heading says is left
    out: the reading site takes the title from that heading, and both would
    show it twice."""
    if FRONT_MATTER.match(text):
        return text
    try:
        with open(old_path, encoding="utf-8", errors="replace", newline="") as fh:
            old = FRONT_MATTER.match(fh.read())
    except OSError:
        return text
    if not old:
        return text
    block = old.group(0) if old.group(1) else old.group(0) + "\n"
    h1 = re.search(r"(?m)^# +(.+?)\s*#*\s*$", text)
    if h1:
        plain = lambda t: " ".join(re.sub(r"[*_`]", "", t).split())
        heading = plain(h1.group(1))
        lines = block.splitlines(keepends=True)
        kept = [ln for ln in lines[1:-1]
                if not (re.match(r"title\s*:", ln)
                        and plain(ln.split(":", 1)[1].strip().strip("\"'")) == heading)]
        if not any(ln.strip() for ln in kept):
            return text
        block = lines[0] + "".join(kept) + lines[-1]
    return block + "\n" + text.lstrip("\n")


def _collect_media(stage, text, link_prefix):
    """Flatten pandoc's extra folder level, point the links at `assets`, and say
    what came out.

    pandoc puts a picture at `<STAGE_MEDIA>/media/name.png`, because that is
    where it sat inside the Word file, and writes every link in the chapter to
    match. Two things are wrong with that and both are fixed here. Nobody wants
    a folder called "media" inside the folder, so when that is exactly the shape
    we find, the contents are lifted up one level. And the staging folder is not
    where the pictures are going, so its name is replaced throughout the text by
    the path from the chapter to its folder under `assets`.

    Anything other than that one inner "media" folder is left exactly as pandoc
    arranged it, and the links still point at it - only the prefix moves.

    This is a brand-new file that is not in the vault yet, so rewriting it here
    breaks no promise: the untouched-lines rule applies to chapters that already
    exist.
    """
    full = os.path.join(stage, STAGE_MEDIA)
    if not os.path.isdir(full):
        return text, []

    entries = os.listdir(full)
    inner = os.path.join(full, "media")
    if entries == ["media"] and os.path.isdir(inner):
        for name in os.listdir(inner):
            shutil.move(os.path.join(inner, name), os.path.join(full, name))
        os.rmdir(inner)
        text = text.replace(f"{STAGE_MEDIA}/media/", f"{STAGE_MEDIA}/")

    text = text.replace(f"{STAGE_MEDIA}/", f"{link_prefix}/")

    media = []
    for path in sorted(glob.glob(os.path.join(full, "**", "*"), recursive=True)):
        if os.path.isfile(path):
            media.append({
                "rel": os.path.relpath(path, full),
                "name": os.path.basename(path),
                "ext": os.path.splitext(path)[1].lower().lstrip("."),
                "size": os.path.getsize(path),
            })
    return text, media


# --- writing it into the vault ----------------------------------------------

def name_problem(md_name):
    """Why a chapter cannot have this name anywhere, in the author's words.

    None if it can. This is the part of destination_problem() that holds
    wherever the chapter is going, the drafts area included.
    """
    if not md_name.lower().endswith((".md", ".markdown")):
        return "A chapter's name has to end in .md — that is what Obsidian reads."
    stem = os.path.basename(md_name)
    if stem != md_name or stem in ("", ".", ".."):
        return "Please give just a name, not a path with folders in it."
    if re.search(r'[/\\:*?"<>|]', stem):
        return ('A chapter\'s name cannot contain any of these characters: '
                '/ \\ : * ? " < > |')
    return None


def destination_problem(folder, md_name):
    """Why we cannot write there, in the author's words. None if we can."""
    if not folder or not os.path.isdir(folder):
        raise KeyError("That folder could not be found. Please choose it again.")
    problem = name_problem(md_name)
    if problem:
        return problem
    stem = os.path.basename(md_name)
    target = os.path.join(folder, stem)
    if os.path.exists(target):
        return (f"There is already a chapter called “{stem}” in that folder. "
                "This tool never writes over a file that already exists, and it "
                "has no undo, so please give this one a different name.")
    if not os.access(folder, os.W_OK):
        return ("That folder is read-only, so nothing could be saved into it. "
                "Please choose another one.")

    # The pictures do not go here, so where they do go is checked here too.
    # Finding this out before the conversion rather than after it means the
    # author is never told, at the end of a long import, that it cannot land.
    dest = media_destination(folder, md_name)
    if dest is None:
        return vault_problem(folder)
    if os.path.isdir(dest["target"]) and os.listdir(dest["target"]):
        return (f"There is already a folder of pictures called “{dest['rel']}” "
                "in your textbook, and this chapter's pictures would have to go "
                "into it. Every chapter keeps its pictures in a folder of its "
                "own, because Word calls the pictures in every document by the "
                "same names — image1, image2 — so two chapters sharing a "
                "folder "
                "would write over each other's. Please give this chapter a "
                "different name.")
    if not os.access(os.path.join(dest["vault"], MEDIA_ROOT), os.W_OK):
        return ("Your textbook's “assets” folder is read-only, so this "
                "chapter's pictures could not be saved into it. Nothing has "
                "been written.")
    return None


def save(result, folder):
    """Write the converted chapter and its pictures into the vault.

    The chapter goes in the folder the author chose. The pictures go into the
    vault's `assets` folder under the chapter's own name, which is what the
    links in the chapter already say. See "where the pictures go" above.

    Returns (chapter_path, media_folder_path or None).
    """
    md_name = os.path.basename(result["md_name"])
    problem = destination_problem(folder, md_name)
    if problem:
        raise KeyError(problem)

    chapter_path = os.path.join(folder, md_name)
    media_target = None
    made_media = False

    if result.get("media_target"):
        media_target = result["media_target"]
        # Checked again here, at the moment of writing, and not only in
        # destination_problem above: this tool has no undo, and a folder can
        # appear between the two.
        if os.path.isdir(media_target) and os.listdir(media_target):
            raise KeyError(
                f"There is already a folder of pictures called "
                f"“{result['media_rel']}” in your textbook, and this chapter's "
                "pictures would have to go into it. Nothing was written. "
                "Please give the chapter a different name."
            )
        made_media = not os.path.exists(media_target)
        shutil.copytree(os.path.join(result["stage"], STAGE_MEDIA),
                        media_target, dirs_exist_ok=True)

    # The chapter goes last, so a chapter never exists in the vault pointing at
    # pictures that failed to copy.
    tmp = os.path.join(folder, f".{md_name}.aa-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(result["text"])
        os.replace(tmp, chapter_path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        # Only what we made ourselves is taken away again.
        if made_media and media_target and os.path.isdir(media_target):
            shutil.rmtree(media_target, ignore_errors=True)
        raise
    return chapter_path, media_target


def discard(result):
    if result and result.get("stage"):
        shutil.rmtree(result["stage"], ignore_errors=True)


# --- what to check, in plain language ---------------------------------------
#
# Word documents carry things markdown has no way to hold, and things Word
# itself only pretends to have: a heading that is really bold text, a table
# whose cells have been merged, a chart that is a picture of a chart. pandoc
# does the best it can and says almost nothing about it. This is where we look
# at what actually came out and tell the author, in their own words, which parts
# are worth their eyes.
#
# Every note is written to be read by someone who uses Word and has never seen
# markdown. No note ever tells them to run anything.

HTML_TABLE_RE = re.compile(r"^\s*<table[\s>]", re.I)
HTML_IMG_RE = re.compile(r"<img\s[^>]*src=", re.I)
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
PIPE_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
PIPE_RULE_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:")
ATX_RE = re.compile(r"^(#{1,6})\s+(.*)$")
BOLD_ONLY_RE = re.compile(r"^\*\*(.+?)\*\*:?\s*$")
SPAN_ATTR_RE = re.compile(r"\[[^\]]*\]\{[^}]*\}")
ANCHOR_RE = re.compile(r"\[\]\{#[^}]*\}")
MATHS_RE = re.compile(r"\$`|`\$|\$\$|(?<![\w$])\$[^\s$][^\n$]*\$(?![\w$])")

# Word turns charts, SmartArt, WordArt and pasted Excel ranges into these, and
# nothing on a Mac outside Word and Obsidian's PDF export will show them.
UNVIEWABLE_PICTURE_TYPES = {"emf", "wmf", "bin", "vml"}
VIEWABLE_PICTURE_TYPES = {"png", "jpg", "jpeg", "gif", "svg", "webp", "tiff", "tif"}


def _note(level, headline, body, check=None):
    """level: 'ok' | 'look' | 'warn'. 'look' means it converted, but go and see."""
    return {"level": level, "headline": headline, "body": body,
            "check": check or ""}


def report(result):
    """Everything the author should know before this chapter joins the vault."""
    text = result["text"]
    lines = text.split("\n")
    notes = []

    notes += _pictures_notes(result, text, lines)
    notes += _table_notes(lines)
    notes += _footnote_notes(text, lines)
    notes += _heading_notes(lines)
    notes += _leftovers_notes(text, lines)
    notes += _converter_notes(result)
    notes += _shape_notes(text, lines)

    counts = {
        "lines": len(lines),
        "words": len(text.split()),
        "pictures": len(result.get("media") or []),
        "headings": sum(1 for ln in lines if ATX_RE.match(ln)),
        "pipe_tables": _count_pipe_tables(lines),
        "html_tables": sum(1 for ln in lines if HTML_TABLE_RE.match(ln)),
        "footnotes": len({m.group(1) for ln in lines
                          for m in [FOOTNOTE_DEF_RE.match(ln)] if m}),
    }
    return {"notes": notes, "counts": counts}


# -- pictures -----------------------------------------------------------------

def _pictures_notes(result, text, lines):
    media = result.get("media") or []
    folder = result.get("media_rel")
    out = []

    referenced = set()
    for m in MD_IMG_RE.finditer(text):
        referenced.add(os.path.basename(m.group(1).split()[0].strip("<>")))
    for m in re.finditer(r'<img\s[^>]*src="([^"]+)"', text, re.I):
        referenced.add(os.path.basename(m.group(1)))

    if not media:
        if referenced:
            out.append(_note(
                "warn", "Pictures were mentioned but none came out",
                "The chapter refers to pictures, but no picture files were "
                "produced. This happens when the pictures are linked to files "
                "elsewhere on the Mac rather than stored inside the Word "
                "document itself.",
                "Open the Word document and check whether the pictures show up "
                "there. If they do, copy each one and paste it back in, then "
                "save and bring the file in again."))
        else:
            out.append(_note("ok", "No pictures",
                             "This document had no pictures in it, so there is "
                             "nothing to check.", ""))
        return out

    viewable = [m for m in media if m["ext"] in VIEWABLE_PICTURE_TYPES]
    odd = [m for m in media if m["ext"] in UNVIEWABLE_PICTURE_TYPES]
    other = [m for m in media
             if m["ext"] not in VIEWABLE_PICTURE_TYPES
             and m["ext"] not in UNVIEWABLE_PICTURE_TYPES]

    one = len(media) == 1
    out.append(_note(
        "ok" if not odd else "look",
        ("1 picture was" if one else f"{len(media)} pictures were")
        + " taken out of the Word file",
        ("It is" if one else "They are")
        + " saved in your textbook's pictures folder, in a folder of "
        + ("its" if one else "their")
        + f" own: “{folder}”. That is where every chapter's pictures go, one "
        "folder per chapter — it is where Obsidian looks, where the website "
        "looks, and what a department copies when it makes its own edition of "
        "the book. Obsidian finds "
        + ("it" if one else "them")
        + " there on its own. The names are the ones Word gave them inside the "
        "document, which are not meaningful — you can rename them later if you "
        "like, as long as you change the chapter to match.",
        "Scroll through the chapter below and check that every picture is where "
        "you expect it."))

    if odd:
        names = ", ".join(sorted({m["ext"] for m in odd}))
        out.append(_note(
            "warn",
            ("1 picture came" if len(odd) == 1 else f"{len(odd)} pictures came")
            + " out in a format nothing can display",
            f"These are .{names} files. Word saves charts, SmartArt diagrams, "
            "WordArt and pasted spreadsheet ranges this way. They are not "
            "really pictures — Word draws them itself, so nothing outside Word "
            "can show them, and they will appear as a broken picture in "
            "Obsidian.",
            "In Word, right-click each chart or diagram, choose Copy, then Paste "
            "Special as a Picture (PNG), and save. Bringing the file in again "
            "will then produce a picture that works everywhere."))

    if other:
        out.append(_note(
            "look",
            ("1 file" if len(other) == 1 else f"{len(other)} files")
            + " of an unusual kind came out too",
            "Alongside the pictures were: "
            + ", ".join(sorted({m["ext"] or "no extension" for m in other}))
            + ". These are usually sound clips, embedded documents or fonts.",
            "If you do not recognise them you can delete them from the folder "
            "afterwards."))

    missing = referenced - {m["name"] for m in media}
    if missing:
        out.append(_note(
            "warn", "Some pictures are referred to but were not found",
            ("1 picture named" if len(missing) == 1
             else f"{len(missing)} pictures named")
            + " in the chapter did not come out of the Word file.",
            "Look for a broken picture in Obsidian after saving, and put it back "
            "in by hand."))

    if HTML_IMG_RE.search(text) or "<figure>" in text:
        out.append(_note(
            "look", "Pictures are written as web tags, not as plain markdown",
            "Where a picture has a caption, or sits inside a paragraph, it comes "
            "out written like a web page — <img src=…> — rather than in "
            "markdown's own short form. This is normal and correct: Obsidian "
            "displays these properly in reading view. They only look like code "
            "while you are editing.",
            "Nothing to do. Switch Obsidian to reading view if the editing view "
            "looks alarming."))
    return out


# -- tables -------------------------------------------------------------------

def _count_pipe_tables(lines):
    count, inside = 0, False
    for ln in lines:
        if PIPE_TABLE_RE.match(ln):
            if not inside:
                count += 1
                inside = True
        else:
            inside = False
    return count


def _table_notes(lines):
    pipe = _count_pipe_tables(lines)
    html = sum(1 for ln in lines if HTML_TABLE_RE.match(ln))
    out = []
    if not pipe and not html:
        return out

    if pipe:
        out.append(_note(
            "look",
            ("1 table converted cleanly" if pipe == 1
             else f"{pipe} tables converted cleanly"),
            ("It came out as a proper markdown table and will look right in "
             "Obsidian." if pipe == 1 else
             "They came out as proper markdown tables and will look right in "
             "Obsidian.")
            + " Column widths are not kept — markdown has no way to hold "
            "them — and any colouring or shading in the Word table is gone.",
            "Check that no column has ended up empty, and that a table with a "
            "header row still has its header."))

    if html:
        out.append(_note(
            "warn",
            ("1 table could not be made into a proper table" if html == 1
             else f"{html} tables could not be made into proper tables"),
            "A table comes out this way when its cells have been merged, or when "
            "a single cell holds more than one paragraph. Markdown tables cannot "
            "do either, so the table is written as a block of web markup "
            "instead. Obsidian still shows it as a table in reading view, and "
            "none of your text is lost — but it is unpleasant to edit, and this "
            "tool's citation and concept-page checks skip over it entirely, so "
            "nothing inside it will ever be linked.",
            "Look at each of these in the preview below. If the table is simple "
            "enough, it is worth unmerging the cells in Word and bringing the "
            "file in again; if it is genuinely complicated, leave it and accept "
            "that its contents will not be linked."))
    return out


# -- footnotes ----------------------------------------------------------------

def _footnote_notes(text, lines):
    defs = {m.group(1) for ln in lines for m in [FOOTNOTE_DEF_RE.match(ln)] if m}
    refs = set()
    for i, ln in enumerate(lines):
        if FOOTNOTE_DEF_RE.match(ln):
            continue
        refs |= {m.group(1) for m in FOOTNOTE_REF_RE.finditer(ln)}
    if not defs and not refs:
        return []

    out = [_note(
        "look",
        ("1 footnote came across" if len(defs) == 1
         else f"{len(defs)} footnotes came across"),
        "Word keeps footnotes at the foot of each page. Markdown has no pages, "
        "so they are all collected at the very bottom of the chapter, and each "
        "one is numbered in the text like this: [^1]. Obsidian shows them as "
        "proper footnotes when you read the chapter.",
        "Scroll to the end of the preview and check that the last footnote is "
        "there and complete. Also check that any endnotes you had have not "
        "quietly merged into the same list.")]

    orphan_refs = refs - defs
    orphan_defs = defs - refs
    if orphan_refs or orphan_defs:
        bits = []
        if orphan_refs:
            n = len(orphan_refs)
            bits.append(f"{n} footnote number{'' if n == 1 else 's'} in the text "
                        f"with no note at the bottom")
        if orphan_defs:
            n = len(orphan_defs)
            bits.append(f"{n} note{'' if n == 1 else 's'} at the bottom that "
                        f"nothing in the text points to")
        out.append(_note(
            "warn", "Some footnotes do not match up",
            "The chapter has " + " and ".join(bits) + ".",
            "This usually means a footnote was deleted in Word without its "
            "number being removed, or the other way round. Find them in the "
            "preview and tidy them up after saving."))
    return out


# -- headings -----------------------------------------------------------------

def _heading_notes(lines):
    levels = []
    for ln in lines:
        m = ATX_RE.match(ln)
        if m:
            levels.append(len(m.group(1)))
    out = []

    bold_alone = 0
    for i, ln in enumerate(lines):
        if not BOLD_ONLY_RE.match(ln.strip()):
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        next_blank = i == len(lines) - 1 or not lines[i + 1].strip()
        if prev_blank and next_blank and len(ln.strip()) < 90:
            bold_alone += 1

    if not levels:
        out.append(_note(
            "warn", "No headings came across at all",
            "Nothing in this chapter became a heading. That almost always means "
            "the headings in the Word document were made by hand — by making the "
            "text bigger and bold — rather than with Word's own Heading styles. "
            "Word treats those as ordinary paragraphs, so that is what they have "
            "become here."
            + (" One line in the chapter looks like a heading written that "
               "way." if bold_alone == 1 else
               f" {bold_alone} lines in the chapter look like headings written "
               "that way." if bold_alone else ""),
            "You can either put a # in front of each heading in Obsidian "
            "afterwards, or go back to Word, apply the Heading 1 / Heading 2 "
            "styles from the Styles gallery, save, and bring the file in again. "
            "The second way is quicker if there are many."))
        return out

    tally = {}
    for lv in levels:
        tally[lv] = tally.get(lv, 0) + 1
    shape = ", ".join(f"{tally[lv]} at level {lv}" for lv in sorted(tally))
    out.append(_note(
        "ok",
        ("1 heading came across" if len(levels) == 1
         else f"{len(levels)} headings came across"),
        f"{shape}. Level 1 is a “#”, level 2 a “##”, and so on — the same as "
        "Word's Heading 1, Heading 2 and Heading 3.",
        "Check in the preview that your section headings are all there."))

    if tally.get(1, 0) > 1:
        out.append(_note(
            "look", f"There are {tally[1]} top-level headings",
            "A chapter usually has one title and everything else beneath it. "
            "Several top-level headings normally means Word's Title and "
            "Heading 1 styles were both used for the same kind of thing.",
            "If some of these should be sections rather than chapter titles, add "
            "an extra # in front of them in Obsidian."))

    skipped = sorted({lv for lv in tally
                      if lv > 1 and (lv - 1) not in tally})
    if skipped:
        out.append(_note(
            "look", "A heading level was skipped",
            "The chapter jumps straight to level "
            + " and ".join(str(s) for s in skipped)
            + " without a level above it. Obsidian's outline will look oddly "
            "indented, though nothing is broken.",
            "Worth a glance at the outline pane in Obsidian afterwards."))

    if bold_alone:
        out.append(_note(
            "look",
            ("1 line may be a heading that did not convert" if bold_alone == 1
             else f"{bold_alone} lines may be headings that did not convert"),
            ("It is a line that stands" if bold_alone == 1
             else "These are lines that stand")
            + " alone and entirely in bold, which is what a heading written by "
            "hand in Word looks like after conversion. "
            + ("It may equally be" if bold_alone == 1 else "They may equally be")
            + " genuine emphasis.",
            ("Look for it" if bold_alone == 1 else "Look for them")
            + " in the preview. Where one really is a heading, put the right "
            "number of # marks in front of it in Obsidian."))
    return out


# -- everything else worth a look --------------------------------------------

def _leftovers_notes(text, lines):
    out = []

    anchors = len(ANCHOR_RE.findall(text))
    spans = len(SPAN_ATTR_RE.findall(text)) - anchors
    if anchors:
        out.append(_note(
            "look",
            ("1 invisible bookmark came across" if anchors == 1
             else f"{anchors} invisible bookmarks came across"),
            "These appear in the text as []{#something}. They are Word bookmarks "
            "and cross-reference targets. They show nothing when the chapter is "
            "read, and they do no harm.",
            "Ignore them, or delete them once you are sure nothing in the "
            "chapter refers to them."))
    if spans > 0:
        out.append(_note(
            "look",
            ("1 piece of text carries Word formatting markdown cannot hold"
             if spans == 1 else
             f"{spans} pieces of text carry Word formatting markdown cannot hold"),
            "These appear as [text]{.underline} or similar. Underlining, "
            "coloured text, small capitals and highlighting have no markdown "
            "equivalent, so pandoc leaves a marker rather than losing the fact "
            "that they were there.",
            "Decide what each was for. Underlined text is usually meant to be "
            "italic, which is *text* in markdown."))

    if MATHS_RE.search(text):
        out.append(_note(
            "look", "There is mathematics in this chapter",
            "Equations written with Word's equation editor come across as "
            "markdown maths, between dollar signs. Obsidian displays them. "
            "Equations that were pasted in as pictures stay pictures.",
            "Check each equation in the preview — the conversion is usually "
            "right but occasionally loses a superscript."))

    if re.search(r"^\s*\\\s*$", text, re.M):
        out.append(_note(
            "look", "There are hard line breaks in the text",
            "A lone backslash at the end of a line is a line break that was "
            "forced in Word with Shift and Return. It still works, but it can "
            "look like a stray mark while editing.",
            "Nothing to do unless one is somewhere unexpected."))

    if "<div" in text or ":::" in text:
        out.append(_note(
            "look", "Some blocks of the document have no markdown equivalent",
            "Text boxes, sidebars and content controls come across as blocks of "
            "web markup rather than plain paragraphs. Nothing is lost, but they "
            "read awkwardly.",
            "Find them in the preview and decide whether the text inside should "
            "simply become ordinary paragraphs."))

    if not any(re.match(r"^\s{0,3}#{1,6}\s*\**\s*"
                        r"(references?|bibliography|works\s+cited|reference\s+list)",
                        ln, re.I) for ln in lines):
        out.append(_note(
            "look", "This chapter has no References section",
            "None of the headings is called References or Bibliography. The "
            "citation check works by matching “(Bhaskar, 1975)” against a "
            "reference list in the same chapter, so with no such section it will "
            "find nothing to link.",
            "If the chapter does have a reference list under a different "
            "heading, rename that heading to References in Obsidian."))
    return out


def _converter_notes(result):
    """Anything the converter itself complained about.

    It is usually silent, so when it does say something it nearly always means a
    part of the document was skipped rather than converted. The author is shown
    the words verbatim - they are not written for them, and pretending otherwise
    would be worse than admitting it - but told plainly what they mean.
    """
    warnings = [w for w in (result.get("pandoc_warnings") or []) if w.strip()]
    if not warnings:
        return []
    return [_note(
        "warn",
        ("The converter reported a problem" if len(warnings) == 1
         else f"The converter reported {len(warnings)} problems"),
        "It does not usually say anything, so this normally means a part of the "
        "document was skipped rather than converted. Its own words, which are "
        "not written for you: " + "  ·  ".join(w.strip() for w in warnings),
        "Compare the chapter below against the Word document, looking for "
        "anything that is missing. If you cannot see what it means, send this "
        "wording to the technical contact.")]


def _shape_notes(text, lines):
    """The thing the whole tool depends on: one paragraph, one line.

    --wrap=none makes this true, so this is a check that the flag did its job
    rather than a real expectation of failure. It only speaks up when there is
    enough prose to judge: a short document can honestly have no long lines.
    """
    prose = [ln for ln in lines if ln.strip()
             and not ln.lstrip().startswith(("#", "|", ">", "<", "-", "*", "[^"))]
    if not prose:
        return []
    longest = max(len(ln) for ln in prose)
    enough_to_judge = len(prose) >= 10 and sum(len(ln) for ln in prose) > 1200

    if enough_to_judge and longest < 90:
        return [_note(
            "warn", "The paragraphs may have been broken up",
            "Every paragraph should be on one long line, however far it runs. "
            "None of the paragraphs here is longer than 90 characters, which "
            "suggests they have been split across several lines. The tool still "
            "works, but your vault's history would then show a whole paragraph "
            "as changed whenever a single word is corrected.",
            "Tell the technical contact if you see this — it means something "
            "about the conversion needs looking at.")]
    return [_note(
        "ok", "Each paragraph is on a single line",
        "This is what the rest of the tool expects, and it is what keeps your "
        "vault's history readable: correcting one word later shows up as one "
        "changed line rather than a rewritten paragraph.", "")]
