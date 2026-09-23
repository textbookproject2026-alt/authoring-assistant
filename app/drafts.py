"""
Writing to the book's drafts branch: a Word import, a tidy of a chapter's
citations, links and glossary, and an accepted reader's suggestion.

The drafts branch is the author's folder on the service: accepted changes land
there, a browser edit lands there once it is published, and "Going live"
carries it to readers. Each piece of work here reaches it as one commit, made
as the signed-in author, on top of the drafts branch exactly as it was read,
and the branch is moved without force. If anything else moved it in the
meantime the service refuses, nothing is written, and the author is shown
the drafts area again.

An edit (a tidy, a suggestion) sends only the files it changed, and inside
them only the lines it changed: the rest of each file goes back byte for byte
as it was read (session.py and console.py prove that before anything is sent).

For a Word import, four rules shape it.

  * Only the drafts branch. The live branch is never named here; a book whose
    two branches are the same is refused outright.
  * One commit, on top of the drafts branch exactly as it was read. The
    branch is then moved without force, so if anything else moved it in the
    meantime the service refuses, and nothing anyone else wrote is lost. The
    author is told, the branch is read again, and they are offered the send
    again with what is there now in front of them.
  * The chapter replaces the one of the same name on drafts, if there is one,
    and the chapter's pictures folder ends up holding exactly this import's
    pictures: a picture taken out of the Word file since the last import is
    taken out of the folder too. The author sees both before sending, and a
    replacement needs its own tick.
  * A pictures folder already holding files on drafts, with no chapter of that
    name beside it, belongs to something else and is never written into.
"""

import difflib
import hashlib
import io
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor

from . import convert, github

MODE_FILE = "100644"


def blob_sha(data):
    """The name the service gives a file's contents, worked out here."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def repo_paths(root, folder, md_name):
    """Where the chapter and its pictures folder sit in the book's repository.

    `root` is the top of the checkout, `folder` where the author chose to put
    the chapter. Returns (chapter path, pictures folder), both with "/", or
    None when either would be outside the checkout.
    """
    dest = convert.media_destination(folder, md_name)
    if dest is None:
        return None
    chapter = os.path.join(os.path.abspath(folder), os.path.basename(md_name))
    out = []
    for path in (chapter, dest["target"]):
        rel = os.path.relpath(path, os.path.abspath(root))
        if rel == "." or rel.startswith("..") or os.path.isabs(rel):
            return None
        out.append(rel.replace(os.sep, "/"))
    return tuple(out)


def pictures(result):
    """{path inside the pictures folder: bytes} for what the import pulled out."""
    base = os.path.join(result["stage"], convert.STAGE_MEDIA)
    out = {}
    for item in result.get("media") or []:
        with open(os.path.join(base, item["rel"]), "rb") as fh:
            out[item["rel"].replace(os.sep, "/")] = fh.read()
    return out


def build_tree(chapter_path, chapter_bytes, media_dir, new_pictures, on_drafts):
    """What has to change on drafts, as tree entries.

    `on_drafts` is {path: blob sha} for the chapter and everything under its
    pictures folder as drafts holds them now. Returns a list of
    {"path", "sha", "data"}: `data` is the bytes to upload, or None with a
    None `sha` for a file to take away. A file already there with the same
    contents is left out, so sending the same import twice changes nothing.
    """
    entries = []

    def put(path, data):
        sha = blob_sha(data)
        if on_drafts.get(path) != sha:
            entries.append({"path": path, "sha": sha, "data": data})

    put(chapter_path, chapter_bytes)
    keep = set()
    for rel, data in sorted(new_pictures.items()):
        path = f"{media_dir}/{rel}"
        keep.add(path)
        put(path, data)
    for path in sorted(on_drafts):
        if path.startswith(media_dir + "/") and path not in keep:
            entries.append({"path": path, "sha": None, "data": None})
    return entries


def _together(*calls):
    """Run calls to the service at the same time, and return their answers in
    order. Each call takes about three seconds from the author's Mac, so
    calls that don't need each other's answers are never made one by one."""
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def _listing(token, book, also=None):
    """(head, tree sha, {path: tree entry}) for drafts as it is now, or a Problem.

    One round trip in the ordinary case: the branch's commit and tree, and
    the whole listing, are asked for together by the branch's name, and the
    listing is used only if it is of that very commit. If the branch moved
    between the two answers, the listing is asked for again by the commit.

    `also(ref)` is one more call made alongside, given the branch's name (or
    its commit, when asked again); its answer comes back as a fourth item,
    for the caller to check against the listing.
    """
    ref = book.drafts_branch
    calls = [lambda: github.branch_tip(token, book),
             lambda: github.whole_tree(token, book, ref)]
    if also is not None:
        calls.append(lambda: also(ref))
    tip, tree, *extra = _together(*calls)
    if isinstance(tip, github.Problem):
        return tip
    head, tree_sha = tip
    if isinstance(tree, github.Problem) or tree.get("sha") not in (head, tree_sha):
        calls = [lambda: github.whole_tree(token, book, head)]
        if also is not None:
            calls.append(lambda: also(head))
        tree, *extra = _together(*calls)
    if isinstance(tree, github.Problem):
        return tree
    if tree.get("truncated"):
        return github.Problem(
            "The book is too large for the drafts area to be checked in one "
            "go, so nothing was sent. Ask the technical contact."
        )
    files = {e.get("path"): e for e in tree.get("tree") or []
             if isinstance(e, dict)}
    return (head, tree_sha, files, *extra)


def snapshot(token, book):
    """The drafts branch as it stands, for an edit to be worked out against.

    Returns {"head", "tree", "files"}, `files` being {path: blob sha} for every
    file, or a Problem. Reading only.
    """
    listing = _listing(token, book)
    if isinstance(listing, github.Problem):
        return listing
    return _snap(*listing)


def _snap(head, tree_sha, entries):
    return {"head": head, "tree": tree_sha,
            "files": {p: e["sha"] for p, e in entries.items()
                      if e.get("type") == "blob" and e.get("sha")}}


def snapshot_with(token, book, path):
    """snapshot, and the bytes of one file in it: (snap, bytes or None), or a
    Problem. None when the snapshot has no such file.

    The file is asked for alongside the listing, so this takes no longer than
    the snapshot alone. Its bytes are used only if they are the very file the
    listing names; otherwise it is read again by its hash.
    """
    listing = _listing(token, book, also=lambda ref: github.file_bytes_at(
        token, book, path, ref))
    if isinstance(listing, github.Problem):
        return listing
    head, tree_sha, entries, data = listing
    snap = _snap(head, tree_sha, entries)
    sha = snap["files"].get(path)
    if sha is None:
        return snap, None
    if isinstance(data, github.Problem) or blob_sha(data) != sha:
        data = github.blob_bytes(token, book, sha)
        if isinstance(data, github.Problem):
            return data
    return snap, data


def safe_path(path):
    """True for a path inside the book as the service writes them: no leading
    "/", no "..", no "." and no empty parts. Anything else is never followed."""
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def text_of(data):
    """A file's bytes as text for editing, or None when it is not UTF-8.

    Never decoded with replacements: a character that came back as "?" would
    be sent back as one, changing a line nobody meant to touch.
    """
    try:
        return data.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None


def edit_entries(snap, changed):
    """Tree entries for an edit: {path: new bytes} for the files it touched.

    A file whose new contents are what drafts already holds is left out, so an
    edit that changes nothing sends nothing.
    """
    entries = []
    for path, data in sorted(changed.items()):
        sha = blob_sha(data)
        if snap["files"].get(path) != sha:
            entries.append({"path": path, "sha": sha, "data": data})
    return entries


def read(token, book, chapter_path, media_dir):
    """What drafts holds now where this chapter is going.

    Returns a state dict, or a Problem. `refused` is set, in the author's
    words, when the chapter must not be sent there at all.
    """
    listing = _listing(token, book)
    if isinstance(listing, github.Problem):
        return listing
    head, tree_sha, files = listing

    state = {"head": head, "tree": tree_sha, "on_drafts": {},
             "exists": False, "last": None, "old_text": None, "refused": None}
    chapter = files.get(chapter_path)
    if chapter is not None and chapter.get("type") != "blob":
        state["refused"] = (
            f"The drafts area has a folder called “{chapter_path}”, where this "
            "chapter would go. Nothing was sent. Please give the chapter a "
            "different name.")
        return state
    folder = files.get(media_dir)
    if folder is not None and folder.get("type") != "tree":
        state["refused"] = (
            f"The drafts area has a file called “{media_dir}”, where this "
            "chapter's pictures would go. Nothing was sent. Please give the "
            "chapter a different name.")
        return state

    on = {p: e["sha"] for p, e in files.items()
          if p.startswith(media_dir + "/") and e.get("type") == "blob"}
    if chapter is None and on:
        state["refused"] = (
            f"The drafts area already has a folder of pictures called "
            f"“{media_dir}”, but no chapter of this name beside it, so those "
            "pictures belong to something else. Nothing was sent. Please give "
            "the chapter a different name.")
        return state
    if chapter is not None:
        on[chapter_path] = chapter["sha"]
        state["exists"] = True
        state["last"] = github.last_change(token, book, chapter_path, head)
        old = github.blob_text(token, book, chapter["sha"])
        state["old_text"] = None if isinstance(old, github.Problem) else old
    state["on_drafts"] = on
    return state


def describe(book, state, entries, chapter_path, media_dir, new_text):
    """What the author is shown before sending."""
    out = {
        "repo": book.repo,
        "branch": book.drafts_branch,
        "chapter_path": chapter_path,
        "media_dir": media_dir,
        "refused": state["refused"],
        "exists": state["exists"],
        "last": state["last"],
        "nothing_to_send": not entries and not state["refused"],
        "removed": [e["path"][len(media_dir) + 1:] for e in entries
                    if e["data"] is None],
        "changed_lines": None,
    }
    if state["exists"] and state["old_text"] is not None:
        old, new = state["old_text"].splitlines(), new_text.splitlines()
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        out["changed_lines"] = {
            "removed": sum(i2 - i1 for tag, i1, i2, _, _ in matcher.get_opcodes()
                           if tag in ("replace", "delete")),
            "added": sum(j2 - j1 for tag, _, _, j1, j2 in matcher.get_opcodes()
                         if tag in ("replace", "insert")),
        }
    return out


MOVED = (
    "Nothing was sent. Something else changed the drafts area while you were "
    "looking at this chapter — a browser edit being published, or an accepted "
    "change — and this tool never writes over anyone else's work. It has "
    "looked again, and what is there now is shown below. Read it, then press "
    "“Send to drafts” again."
)

MOVED_EDIT_SAME = (
    "Nothing was sent. Something else changed the drafts area while you were "
    "working — a browser edit being published, or an accepted change — and "
    "this tool never writes over anyone else's work. It has looked again: "
    "this chapter and the glossary are as they were, so your choices still "
    "stand. Look at the changes once more, then send them again."
)

MOVED_EDIT_CHANGED = (
    "Nothing was sent. Something else changed this chapter or the glossary in "
    "the drafts area while you were working — a browser edit being published, "
    "or an accepted change — and this tool never writes over anyone else's "
    "work. It has read the chapter again as it is now. Please go through it "
    "again."
)


def send(token, book, state, entries, message):
    """Make the one commit and move the drafts branch to it.

    Returns {"sha", "url"}, {"moved": True} when drafts moved after it was read
    (nothing was changed), or a Problem.
    """
    if book.drafts_branch == book.live_branch:
        return github.Problem(
            f"“{book.title}” has no drafts area separate from what readers "
            "see, so nothing was sent. Ask the technical contact.")
    if not entries:
        return github.Problem("The drafts area already has exactly this, so "
                              "there was nothing to send.")

    tree_entries = []
    for entry in entries:
        sha = None
        if entry["data"] is not None:
            blob = github.create_blob(token, book, entry["data"])
            if isinstance(blob, github.Problem):
                return blob
            sha = blob.get("sha")
            if sha != entry["sha"]:
                return github.Problem(
                    "A file came back different from the one sent. Nothing "
                    "was changed in the drafts area.")
        tree_entries.append({"path": entry["path"], "mode": MODE_FILE,
                             "type": "blob", "sha": sha})

    tree = github.create_tree(token, book, state["tree"], tree_entries)
    if isinstance(tree, github.Problem):
        return tree
    commit = github.create_commit(token, book, message, tree.get("sha"),
                                  state["head"])
    if isinstance(commit, github.Problem):
        return commit

    moved = github.move_drafts(token, book, commit.get("sha"))
    if isinstance(moved, github.Problem):
        if moved.code in (409, 422):
            now = github.branch_head(token, book)
            if isinstance(now, str) and now != state["head"]:
                return {"moved": True}
        return moved
    return {"sha": commit.get("sha"), "url": commit.get("html_url")}


MOVED_SUGGESTION = (
    "Nothing was changed, and the suggestion is still open. Something else "
    "changed the drafts area while you were looking at it — a browser edit "
    "being published, or an accepted change — and this tool never writes over "
    "anyone else's work. It has read the chapter again; what accepting would "
    "do now is shown below. Accept again if it is still right."
)


# --- "Download a copy" ---------------------------------------------------------

class CopyError(Exception):
    """The copy could not be made. The message is for the author."""


def unpack_copy(archive, parent, name):
    """Unpack the drafts branch's archive into a new folder `name` in `parent`.

    The folder is always new: if one of that name is there, " 2", " 3"… is
    added, so nothing already on this Mac is written over. Only ordinary files
    are written, and only inside the new folder; links, and any path that
    would lead out of it, are left out and counted. Returns {"folder",
    "files", "left_out"}.
    """
    if not os.path.isdir(parent):
        raise CopyError("That folder can't be found. Nothing was written.")
    folder = os.path.join(parent, name)
    n = 2
    while os.path.exists(folder):
        folder = os.path.join(parent, f"{name} {n}")
        n += 1
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
    except (tarfile.TarError, OSError):
        raise CopyError("What came back wasn't a copy of the book. Nothing "
                        "was written.")
    os.makedirs(folder)
    files = left_out = 0
    with tar:
        for member in tar:
            # Every path starts with one folder the service names after the
            # commit; the book's own files are inside it.
            parts = member.name.split("/")[1:]
            if not parts or parts == [""] or member.isdir():
                continue
            if not member.isfile() or not all(
                    p not in ("", ".", "..") for p in parts):
                left_out += 1
                continue
            target = os.path.join(folder, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            source = tar.extractfile(member)
            with open(target, "xb") as fh:
                fh.write(source.read())
            files += 1
    return {"folder": folder, "files": files, "left_out": left_out}
