"""
Which textbooks exist, and which one a vault on this Mac belongs to.

The platform keeps one list of textbooks, the registry, in its own repository.
Every fact the console needs about a book comes from there: the repository its
text lives in, the drafts and live branches, and the site readers visit. None of
it is written into this app, so a new book needs no new release.

Where the list comes from, in order:

  1. the registry repository, fetched once each time the app starts;
  2. the last copy that was fetched successfully, kept in Application Support;
  3. a copy bundled into the app when it was built.

Whenever the list did not come from (1), the interface says how old it is.

A vault says which book it is in two independent ways: the `slug` in its
`textbook.config.json`, and the repository its git `origin` remote points at.
The two must agree with the registry before the console will write anything
into that vault (see `identify`). That is the check that keeps one book's text
from being written into another book's chapter.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import config

# Where the list lives. The repository is public, so no sign-in is needed to
# read it. AA_REGISTRY_URL points a development copy at a different list.
REGISTRY_URL = os.environ.get(
    "AA_REGISTRY_URL",
    "https://raw.githubusercontent.com/"
    "textbookproject2026-alt/textbook-registry/main/registry.json",
)
CACHE_FILE = os.path.join(config.SUPPORT_DIR, "registry.json")
BUNDLED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "registry.bundled.json")

SCHEMA_VERSION = 1
FETCH_TIMEOUT = 8
CONFIG_NAME = "textbook.config.json"

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
STATUSES = ("preview", "live", "retired")


class RegistryError(Exception):
    """The list could not be used. The message is for the author."""


class Book:
    """One textbook, with every per-book value the console uses.

    Anything that can be worked out from a stored fact is worked out here and
    never stored, so there is one place to get each of them right.
    """

    def __init__(self, entry):
        content = entry["content"]
        site = entry.get("site") or {}
        self.slug = entry["slug"]
        self.status = entry["status"]
        self.title = entry.get("title") or self.slug
        self.repo = content["repo"]
        self.owner, self.name = self.repo.split("/", 1)
        self.live_branch = content["live_branch"]
        self.drafts_branch = content["drafts_branch"]
        self.domain = site.get("domain")
        host = site.get("host")
        # How readers are served: "obsidian-publish" while a book is still on
        # Publish, which uploads from the author's own folder. Anything else is
        # built from the repository.
        self.host_kind = host.get("kind") if isinstance(host, dict) else None

    @property
    def from_folder(self):
        """True while readers see what is published from the author's folder."""
        return self.host_kind == "obsidian-publish"

    @property
    def origin(self):
        return f"https://{self.domain}" if self.domain else None

    @property
    def discussion_url(self):
        """Every comment left in the margins of this book's site."""
        if not self.domain:
            return None
        return f"https://hypothes.is/search?q=url:{self.origin}/*"

    @property
    def history_url(self):
        return f"https://github.com/{self.repo}/commits/{self.live_branch}"

    def same_repo(self, other):
        """Repository names are compared the way GitHub compares them."""
        return bool(other) and other.casefold() == self.repo.casefold()

    def describe(self):
        """What the interface is told about this book."""
        return {
            "slug": self.slug,
            "title": self.title,
            "status": self.status,
            "repo": self.repo,
            "live_branch": self.live_branch,
            "drafts_branch": self.drafts_branch,
            "site": self.origin,
            "discussion_url": self.discussion_url,
            "history_url": self.history_url,
            "from_folder": self.from_folder,
        }

    def __repr__(self):
        return f"Book({self.slug!r}, {self.repo!r})"


class Registry:
    def __init__(self, data, source, fetched_at=None, problem=None):
        self.platform = data.get("platform") or {}
        self.books = [Book(b) for b in data["books"]]
        self.source = source          # "live", "cached" or "bundled"
        self.fetched_at = fetched_at  # seconds since the epoch, or None
        self.problem = problem        # why a fresh copy is not being used

    def find(self, slug):
        """The book with this slug, or None. A retired book is not found."""
        for book in self.books:
            if book.slug == slug and book.status != "retired":
                return book
        return None

    def resolve(self, slug):
        """The book with this slug, or RegistryError. Never another book."""
        if not slug:
            raise RegistryError("No textbook was named.")
        book = self.find(slug)
        if book is None:
            raise RegistryError(
                f"“{slug}” isn't a registered textbook"
                + (" any more." if any(b.slug == slug for b in self.books) else ".")
            )
        return book

    def active(self):
        """Books the console can work on: everything that is not retired."""
        return [b for b in self.books if b.status != "retired"]

    @property
    def client_id(self):
        return (self.platform.get("console_oauth_client_id") or "").strip()

    def describe(self):
        return {
            "source": self.source,
            "fresh": self.source == "live",
            "as_of": _day(self.fetched_at),
            "problem": self.problem,
        }


def _day(stamp):
    if not stamp:
        return None
    return time.strftime("%-d %B %Y", time.localtime(stamp))


# --- reading and checking the list -------------------------------------------

def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise RegistryError(f"The list of textbooks names “{key}” twice.")
        seen[key] = value
    return seen


def parse(text):
    """Parse and check a registry. Returns the data, or raises RegistryError.

    The registry's own CI checks far more than this. These are the checks this
    app relies on, repeated here because a list that fails them could send the
    console to the wrong repository, and that is not worth trusting CI for.
    """
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError:
        raise RegistryError("The list of textbooks could not be read.")
    if not isinstance(data, dict):
        raise RegistryError("The list of textbooks is not in the expected shape.")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError(
            "The list of textbooks is in a newer format than this copy of the "
            "app understands. Ask the technical contact for an update."
        )
    books = data.get("books")
    if not isinstance(books, list):
        raise RegistryError("The list of textbooks has no books in it.")

    slugs, repos = set(), set()
    for entry in books:
        where = entry.get("slug") if isinstance(entry, dict) else None
        try:
            slug = entry["slug"]
            status = entry["status"]
            content = entry["content"]
            repo = content["repo"]
            live = content["live_branch"]
            drafts = content["drafts_branch"]
        except (KeyError, TypeError):
            raise RegistryError(
                f"An entry in the list of textbooks ({where or 'unnamed'}) is "
                "missing something the console needs."
            )
        site = entry.get("site") or {}
        domain = site.get("domain") if isinstance(site, dict) else None
        if not (isinstance(slug, str) and SLUG_RE.match(slug)):
            raise RegistryError(f"“{slug}” is not a valid textbook name.")
        if status not in STATUSES:
            raise RegistryError(f"“{slug}” has an unknown status.")
        if not (isinstance(repo, str) and REPO_RE.match(repo)):
            raise RegistryError(f"“{slug}” names its repository wrongly.")
        if not (isinstance(live, str) and live and isinstance(drafts, str)
                and drafts and live != drafts):
            raise RegistryError(
                f"“{slug}” needs a drafts branch and a live branch, and they "
                "must be different."
            )
        if domain is not None and not isinstance(domain, str):
            raise RegistryError(f"“{slug}” names its site wrongly.")
        if slug in slugs:
            raise RegistryError(f"The list of textbooks names “{slug}” twice.")
        if repo.casefold() in repos:
            raise RegistryError(
                f"Two textbooks in the list share the repository {repo}."
            )
        slugs.add(slug)
        repos.add(repo.casefold())
    return data


def _fetch():
    req = urllib.request.Request(REGISTRY_URL, headers={
        "User-Agent": "Authoring-Assistant",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def _read_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            wrapper = json.load(fh)
        return parse(json.dumps(wrapper["registry"])), wrapper.get("fetched_at")
    except (OSError, ValueError, KeyError, TypeError, RegistryError):
        return None, None


def _write_cache(data, fetched_at):
    try:
        config.ensure_dir()
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": fetched_at, "registry": data}, fh, indent=2)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass  # a missing cache only costs the offline fallback


def _read_bundled():
    try:
        with open(BUNDLED_FILE, "r", encoding="utf-8") as fh:
            text = fh.read()
        return parse(text), os.path.getmtime(BUNDLED_FILE)
    except (OSError, RegistryError):
        return None, None


def load(fetch=_fetch):
    """Fetch the list, falling back to the saved and bundled copies.

    Returns a Registry, or raises RegistryError when no usable copy exists at
    all, in which case no book can be chosen and the console says why.
    """
    problem = None
    try:
        data = parse(fetch())
        now = time.time()
        _write_cache(data, now)
        return Registry(data, "live", now)
    except RegistryError as e:
        # A list that arrived but is broken is never cached over a good one.
        problem = str(e)
    except (urllib.error.URLError, OSError, ValueError):
        problem = "The list of textbooks could not be fetched just now."

    data, stamp = _read_cache()
    if data is not None:
        return Registry(data, "cached", stamp, problem)
    data, stamp = _read_bundled()
    if data is not None:
        return Registry(data, "bundled", stamp, problem)
    raise RegistryError(
        problem + " There is no earlier copy on this Mac, so no textbook can "
        "be chosen until this Mac is online."
    )


_LOCK = threading.Lock()
_CURRENT = {"registry": None, "error": None, "loaded": False}


def get():
    """The list for this run of the app, loaded once. Raises RegistryError."""
    with _LOCK:
        if not _CURRENT["loaded"]:
            try:
                _CURRENT["registry"] = load()
                _CURRENT["error"] = None
            except RegistryError as e:
                _CURRENT["registry"] = None
                _CURRENT["error"] = str(e)
            _CURRENT["loaded"] = True
        if _CURRENT["registry"] is None:
            raise RegistryError(_CURRENT["error"])
        return _CURRENT["registry"]


def retry_if_unavailable():
    """Try again when no copy at all could be had; otherwise keep this run's."""
    with _LOCK:
        if _CURRENT["loaded"] and _CURRENT["registry"] is None:
            _CURRENT["loaded"] = False


def use(registry):
    """Set the list directly. For tests."""
    with _LOCK:
        _CURRENT.update(registry=registry, error=None, loaded=True)


# --- which book a vault belongs to -------------------------------------------

def vault_root(path):
    """The top of the textbook a chosen file or folder sits in.

    The folder holding textbook.config.json, if there is one within a few
    levels; otherwise the top of the git checkout; otherwise the folder itself.
    Suggestions name pages relative to the top of the repository, so this is
    the folder they have to be looked up from.
    """
    start = os.path.abspath(path)
    if not os.path.isdir(start):
        start = os.path.dirname(start)
    for marker in (CONFIG_NAME, ".git"):
        probe = start
        for _ in range(8):
            if os.path.exists(os.path.join(probe, marker)):
                return probe
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
    return start


def _git_config_path(root):
    """Where this checkout's git settings are, following a worktree's pointer."""
    dotgit = os.path.join(root, ".git")
    if os.path.isdir(dotgit):
        return os.path.join(dotgit, "config")
    if not os.path.isfile(dotgit):
        return None
    try:
        with open(dotgit, "r", encoding="utf-8") as fh:
            line = fh.read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(root, gitdir))
    # A worktree keeps its settings in the main checkout; `commondir` says where.
    common = os.path.join(gitdir, "commondir")
    if os.path.isfile(common):
        try:
            with open(common, "r", encoding="utf-8") as fh:
                rel = fh.read().strip()
            gitdir = os.path.normpath(os.path.join(gitdir, rel))
        except OSError:
            return None
    return os.path.join(gitdir, "config")


def origin_repo(root):
    """`owner/name` of the checkout's origin remote, or None.

    The host is ignored: the maintainer's remotes use an SSH alias
    (github-textbook) rather than github.com.
    """
    cfg = _git_config_path(root)
    if not cfg:
        return None
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    section = None
    for raw in lines:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"([^"]*)")?\s*\]$', line)
        if m:
            section = (m.group(1).lower(), m.group(2))
            continue
        if section != ("remote", "origin"):
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() == "url":
            return repo_from_url(value.strip().strip('"'))
    return None


def repo_from_url(url):
    """`owner/name` from any of the ways a GitHub remote can be written."""
    if not url:
        return None
    path = url
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/]*/(.*)$", url)  # https://, ssh://
    if m:
        path = m.group(1)
    else:
        m = re.match(r"^[^/:]+:(.*)$", url)                        # git@host:owner/name
        if m:
            path = m.group(1)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return "/".join(parts)


def read_slug(root):
    """The slug a vault names for itself. (slug, problem)."""
    full = os.path.join(root, CONFIG_NAME)
    if not os.path.isfile(full):
        return None, "missing"
    try:
        with open(full, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, "unreadable"
    slug = data.get("slug") if isinstance(data, dict) else None
    if not isinstance(slug, str) or not slug.strip():
        return None, "no-slug"
    return slug.strip(), None


# The states a vault can be in. Only "ok" lets the console write into it.
OK = "ok"
UNLINKED = "unlinked"            # no textbook.config.json, or no slug in it
NO_REGISTRY = "no_registry"      # names a book, but there is no list to check it
UNKNOWN = "unknown_slug"         # names a book the list does not have
NO_REMOTE = "no_remote"          # names a book, but is not a copy of any repo
MISMATCH = "remote_mismatch"     # names a book, but is a copy of another repo

# States in which the vault claims to be a book and the claim failed. Nothing
# may be written into a vault in one of these states.
FAILED_CLAIMS = (UNKNOWN, NO_REMOTE, MISMATCH)


def identify(root, reg=None):
    """Which book this vault is, checked two independent ways.

    Returns a dict. `state` is one of the constants above, `book` is the Book
    only when the state is OK, and `message` says the rest in plain words.
    `key` is what the vault claims, so a later check can tell whether anything
    changed on disk in the meantime.
    """
    root = os.path.abspath(root)
    name = os.path.basename(root) or root
    slug, why = read_slug(root)
    remote = origin_repo(root)
    out = {"root": root, "name": name, "slug": slug, "remote": remote,
           "book": None, "state": None, "message": "",
           "key": (slug, (remote or "").casefold())}

    if slug is None:
        out["state"] = UNLINKED
        out["message"] = (
            f"The folder “{name}” isn't linked to a registered textbook"
            + (" (its textbook.config.json could not be read)"
               if why == "unreadable" else "")
            + ". The Chapters tools work as normal; the console needs a "
            "vault that belongs to a textbook."
        )
        return out

    if reg is None:
        try:
            reg = get()
        except RegistryError as e:
            out["state"] = NO_REGISTRY
            out["message"] = (
                f"The folder “{name}” says it is “{slug}”, but that can't be "
                f"checked: {e}"
            )
            return out

    book = reg.find(slug)
    if book is None:
        out["state"] = UNKNOWN
        out["message"] = (
            f"The folder “{name}” says it is “{slug}”, which isn't a registered "
            "textbook. Nothing will be written into it until that is put right."
        )
        return out
    if not remote:
        out["state"] = NO_REMOTE
        out["message"] = (
            f"The folder “{name}” says it is “{book.title}”, but it isn't a copy "
            f"of any repository, so that can't be confirmed. “{book.title}” is "
            f"kept in {book.repo}. Nothing will be written into this folder."
        )
        return out
    if not book.same_repo(remote):
        out["state"] = MISMATCH
        out["message"] = (
            f"This vault is a copy of {remote}, but “{book.title}” is kept in "
            f"{book.repo}. Nothing will be written into it until the two agree."
        )
        return out

    out["state"] = OK
    out["book"] = book
    out["message"] = f"The folder “{name}” is “{book.title}”."
    return out


def within(root, path):
    """True if `path` really is inside `root`, links and '..' resolved."""
    try:
        real_root = os.path.realpath(root)
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep)
