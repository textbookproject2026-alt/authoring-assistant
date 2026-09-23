"""
The small local web server behind the interface.

It listens only on this Mac (127.0.0.1), never on the network, and every request
must carry the one-off key printed into the address the browser opens with. That
keeps other programs and web pages on the machine from reaching it.
"""

import json
import os
import posixpath
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (config, console, convert, drafts, github, keychain, llm,
               picker, registry)
from .session import DraftsSession, Session

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")

TOKEN = secrets.token_urlsafe(24)
SESSIONS = {}
LOCK = threading.Lock()
SHUTDOWN = threading.Event()

# The app has no window and no menu bar, so it has to know for itself when it is
# no longer wanted. The page checks in every few seconds; when the checking-in
# stops, the tab has been closed or the browser has quit, and we bow out.
LIFE = {"started": time.time(), "last_ping": None, "closing_at": None}
GRACE_BEFORE_FIRST_PING = 240.0   # the author may take a while to look at it
IDLE_AFTER_LAST_PING = 60.0       # the page has gone away
CLOSING_DELAY = 12.0              # after a goodbye, in case it is just a reload

# Endpoints the page may reach with the key in the address rather than a header,
# because a closing browser tab cannot set headers.
QUERY_TOKEN_ROUTES = {"/api/ping", "/api/bye", "/api/alive"}

# Which copy of the app this process is, read once as it starts: the code it is
# running is the code that was on disk then. The page is stamped with the files
# as they are when it is served and sends that stamp back with every request. A
# newer build copied over a copy that is still running leaves the two apart, and
# without this the only sign was an "Unknown request." for whatever the older
# copy had never heard of.
BUILD = config.build_id()
STALE = ("This window and the part of the app running in the background are "
         "from different versions, so nothing was done. Please quit and reopen "
         "the app.")
# What a page from another version may still ask: to keep the old copy company
# until it is closed, and to quit it.
ANY_BUILD_ROUTES = QUERY_TOKEN_ROUTES | {"/api/quit"}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def free_port(start=8765, tries=60):
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port could be found.")


class Handler(BaseHTTPRequestHandler):
    server_version = "AuthoringAssistant"

    def log_message(self, fmt, *args):
        pass  # the author does not need a server log

    # -- helpers --------------------------------------------------------------

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _query_token(self):
        if "?t=" not in self.path:
            return None
        return self.path.split("?t=")[-1].split("&")[0]

    def _authorised(self, allow_query=False):
        if self.headers.get("X-AA-Token") == TOKEN:
            return True
        if not allow_query:
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost") and self._query_token() == TOKEN

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _session(self, data):
        sid = data.get("session_id")
        with LOCK:
            s = SESSIONS.get(sid)
        if not s:
            raise KeyError("That session has expired. Please start again.")
        return s

    # -- routing --------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/alive":
            if not self._authorised(allow_query=True):
                return self._send(403, {"error": "Not allowed."})
            return self._send(200, {"alive": True})
        if path in ("/", "/index.html"):
            if not self._authorised(allow_query=True):
                return self._send(403, "Not allowed.", "text/plain; charset=utf-8")
            return self._file(os.path.join(WEB, "index.html"))
        if path.startswith("/static/"):
            name = os.path.basename(path)
            full = os.path.join(WEB, name)
            if os.path.isfile(full):
                return self._file(full)
        return self._send(404, "Not found.", "text/plain; charset=utf-8")

    def _file(self, full):
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as fh:
            data = fh.read()
        if ext == ".html":
            data = data.replace(b"__TOKEN__", TOKEN.encode())
            data = data.replace(b"__BUILD__", config.build_id().encode())
        self._send(200, data, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):
        route = self.path.split("?")[0]
        if not self._authorised(allow_query=route in QUERY_TOKEN_ROUTES):
            return self._send(403, {"error": "Not allowed."})
        if route not in ANY_BUILD_ROUTES and \
                self.headers.get("X-AA-Build") != BUILD:
            return self._send(409, {"error": STALE, "stale": True})
        try:
            handler = ROUTES.get(route)
            if not handler:
                return self._send(404, {"error": "Unknown request."})
            return self._send(200, handler(self, self._body()))
        except KeyError as e:
            return self._send(400, {"error": str(e.args[0] if e.args else e)})
        except RuntimeError as e:
            return self._send(409, {"error": str(e)})
        except Exception as e:  # never show the author a stack trace
            return self._send(500, {
                "error": "Something went wrong inside the tool, and nothing was "
                         "changed. The details were: " + str(e)
            })


# --- the requests the interface makes ---------------------------------------

def r_env(handler, data):
    """What the first screen and the settings panel need to know.

    Nothing here can stop the three analyses working: they need no outside
    program at all. pandoc is needed only to bring a Word document in, and the
    interface says so rather than treating it as a prerequisite for everything.
    """
    pandoc = convert.status()
    return {
        "pandoc": pandoc["ready"],
        "pandoc_where": pandoc["where"],
        "pandoc_version": pandoc["version"],
        "deepseek": llm.have_key(),
        "deepseek_hint": llm.key_hint(),
        "obsidian_running": picker.obsidian_running(),
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "version": BUILD.split("+")[0],
        "seen_welcome": config.seen_welcome(),
        "support_dir": config.SUPPORT_DIR,
        "key_store": "this Mac's Keychain",
        "keychain": keychain.available(),
    }


def r_welcome_done(handler, data):
    config.mark_welcome_seen()
    return {"ok": True}


def r_test_key(handler, data):
    """Try a key without saving it, or the saved one if none is given."""
    ok, message = llm.test_key(data.get("key"))
    return {"ok": ok, "message": message}


def r_clear_key(handler, data):
    llm.clear_key()
    return {"cleared": True, "deepseek": llm.have_key()}


def r_ping(handler, data):
    LIFE["last_ping"] = time.time()
    LIFE["closing_at"] = None
    return {"ok": True}


def r_bye(handler, data):
    """The page is going away. Wait a moment in case it is only reloading."""
    LIFE["closing_at"] = time.time() + CLOSING_DELAY
    return {"ok": True}


def r_pick(handler, data):
    kind = data.get("kind", "file")
    start = data.get("start_in") or os.path.expanduser("~")
    if kind == "folder":
        path, err = picker.choose_folder(
            "Choose your vault folder (the folder holding your chapters)", start
        )
    else:
        path, err = picker.choose_file(
            "Choose the chapter you want to work on", start
        )
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    return {"path": path.rstrip("/") if kind == "folder" else path}


def r_open(handler, data):
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        raise KeyError("That file or folder could not be found. Please choose again.")

    if os.path.isfile(path) and not path.lower().endswith((".md", ".markdown")):
        raise KeyError(
            "That is not a markdown chapter. Chapters end in .md - please choose "
            "again."
        )

    session = Session(path)
    chapters = session.chapters()
    if not chapters:
        raise KeyError(
            "There are no markdown chapters in that folder. Please choose the "
            "folder that holds your .md chapter files."
        )

    # Opening a chapter opens its vault for the whole app. A vault that claims
    # a book it can't be shown to be, or is a copy of a book other than the one
    # chosen, is refused here, before any work is done in it.
    ident = _open_vault(session.root)

    sid = secrets.token_urlsafe(12)
    with LOCK:
        SESSIONS[sid] = session
    return {
        "workspace": _workspace_info(),
        "vault": _vault_info(ident),
        "session_id": sid,
        "mode": session.mode,
        "root": session.root,
        "root_name": os.path.basename(session.root) or session.root,
        "chapters": chapters,
        "glossary_path": session.glossary_path,
        "glossary_exists": os.path.exists(session.glossary_path),
    }


def r_prepare(handler, data):
    session = handler._session(data)
    chapter = data.get("chapter")
    if session.mode == "drafts":
        _drafts_session_book(session, data)
        session.load_chapter(chapter)
    else:
        if not chapter or not os.path.isfile(chapter):
            raise KeyError("That chapter could not be found. Please start again.")
        session.load_chapter(chapter)
    warnings, blockers, wrapped = session.preflight(
        we_just_wrote_it=(chapter == IMPORT.get("last_saved"))
    )
    pages, source = session.concept_pages()
    return {
        "mode": session.mode,
        "head": session.snap["head"] if session.mode == "drafts" else None,
        "chapter": chapter,
        "chapter_name": posixpath.basename(chapter) if session.mode == "drafts"
        else os.path.basename(chapter),
        "warnings": warnings,
        "blockers": blockers,
        "hard_wrapped": wrapped,
        "lines": len(session.docmap.lines),
        "has_references": session.docmap.refs_start is not None,
        "concept_pages": [p["title"] for p in pages],
        "concept_source": source,
        "deepseek": llm.have_key(),
    }


def r_analyse(handler, data):
    session = handler._session(data)
    options = {
        "analyses": data.get("analyses", ["references", "terms", "glossary"]),
        "first_mention_only": bool(data.get("first_mention_only", True)),
        "anchor_style": data.get("anchor_style", "obsidian"),
        "use_deepseek": bool(data.get("use_deepseek", False)),
        "concept_folder": data.get("concept_folder"),
    }
    findings, notes = session.run_analyses(options)
    slim = []
    for f in findings:
        slim.append({k: f[k] for k in (
            "id", "kind", "group", "group_label", "line_no", "before", "match",
            "after", "becomes", "occurrence", "occurrence_total", "title",
            "explain", "detail_label", "detail",
        ) if k in f})
    return {"findings": slim, "notes": notes,
            "hidden_later_mentions": session.options.get("first_mention_only", True)}


def r_preview(handler, data):
    session = handler._session(data)
    return session.build_preview(
        data.get("accepted", []), data.get("expand_groups", [])
    )


def r_commit(handler, data):
    session = handler._session(data)
    if session.mode == "drafts":
        return _send_tidy(session, data)
    _guard_vault_write(session.chapter_path, session.glossary_path)
    result = session.commit(data.get("accepted", []), data.get("expand_groups", []))
    return result


def r_savekey(handler, data):
    key = (data.get("key") or "").strip()
    if not key:
        raise KeyError("Please paste a key first.")
    where = llm.save_key(key)
    return {"saved": True, "where": where, "hint": llm.key_hint(),
            "deepseek": llm.have_key()}


# --- a chapter in the drafts area --------------------------------------------
#
# The same three analyses, on a chapter as the chosen book's drafts area holds
# it, with no folder on this Mac needed. What the author says yes to goes back
# as one commit on the drafts branch, made as the signed-in author, on top of
# the drafts branch exactly as it was read (drafts.py). Only the chapter and
# its glossary.md are sent, and in them only the lines that changed.

def _drafts_book(data, doing):
    """(token, book) for work in the chosen book's drafts area.

    An account that can't push to the book is told so here, before any work
    is done, rather than at the end. `doing` finishes the sentence.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    level = _known_access(book.slug)
    if level is None:
        if _ensure_login(token) is None:
            _check_access(token, [book])
        level = _known_access(book.slug)
    if level in ("read", "none"):
        who = f" ({CONSOLE['login']})" if CONSOLE["login"] else ""
        raise KeyError(
            f"Your account{who} can't make changes to “{book.title}” "
            f"({book.repo}), so {doing} The book's maintainer can give you "
            "access, or you can sign in with a different account.")
    return token, book


def _blob_reader(book):
    def read(sha):
        return _unwrap(github.blob_bytes(_token(), book, sha))
    return read


def _drafts_session_book(session, data):
    """The chosen book, and only if it is the book this chapter came from."""
    book = _book_for(data)
    if book.slug != session.book:
        raise KeyError(
            "This chapter was opened from the drafts area of a different book "
            f"from “{book.title}”, the one chosen now. Nothing was done. "
            "Please open it again.")
    return book


def r_drafts_open(handler, data):
    """The chapters in the chosen book's drafts area. Reads; changes nothing."""
    token, book = _drafts_book(
        data, "its drafts area can't be worked on here. Nothing was opened.")
    snap = _unwrap(drafts.snapshot(token, book))
    session = DraftsSession(book.slug, snap, _blob_reader(book))
    chapters = session.chapters()
    if not chapters:
        raise KeyError(f"The drafts area of “{book.title}” has no chapters in "
                       "it yet.")
    sid = secrets.token_urlsafe(12)
    with LOCK:
        SESSIONS[sid] = session
    return {
        "workspace": _workspace_info(),
        "session_id": sid,
        "mode": "drafts",
        "root": None,
        "root_name": f"the drafts area of “{book.title}”",
        "repo": book.repo,
        "branch": book.drafts_branch,
        "head": snap["head"],
        "chapters": chapters,
    }


def _tidy_message(session, preview):
    counts = preview["counts"]
    what = []
    if counts["references"]:
        what.append("citations")
    if counts["terms"] or counts["expanded"]:
        what.append("concept links")
    if counts["glossary"]:
        what.append("glossary")
    return (f"Tidy {posixpath.basename(session.chapter_path)}: "
            f"{', '.join(what) or 'links'}\n\n"
            "Made with the Authoring Assistant.")


def _send_tidy(session, data):
    """One commit on the drafts branch, or a refusal that changed nothing."""
    token = _token()
    if not token:
        _needs_signin()
    book = _drafts_session_book(session, data)
    _require_write(book)
    if data.get("head") != session.snap["head"]:
        raise KeyError("This screen is out of date, so nothing was sent. "
                       "Please look at the changes again.")
    preview, files = session.changes(data.get("accepted", []),
                                     data.get("expand_groups", []))
    entries = drafts.edit_entries(session.snap, files)
    out = {
        "sent": False,
        "written": [e["path"] for e in entries],
        "counts": preview["counts"],
        "glossary_added": preview["glossary_added"],
        "changed_lines": len(preview["changed_lines"]),
        "repo": book.repo,
        "branch": book.drafts_branch,
    }
    if not entries:
        return out

    sent = drafts.send(token, book, session.snap, entries,
                       _tidy_message(session, preview))
    if isinstance(sent, dict) and sent.get("moved"):
        same = session.reload(_unwrap(drafts.snapshot(token, book)))
        return {"moved": True, "same": same, "head": session.snap["head"],
                "message": (drafts.MOVED_EDIT_SAME if same
                            else drafts.MOVED_EDIT_CHANGED)}
    sent = _unwrap(sent)
    # Read again, so a second run on this chapter starts from what was sent.
    fresh = drafts.snapshot(token, book)
    if not isinstance(fresh, github.Problem):
        session.reload(fresh)
    out.update(sent=True, sha=sent["sha"], url=sent.get("url"),
               head=session.snap["head"])
    return out


def r_drafts_download(handler, data):
    """"Download a copy": every file on the chosen book's drafts branch, into a
    new folder on this Mac. Nothing already on the Mac is written over."""
    book = _book_for(data)
    path, err = picker.choose_folder(
        f"Choose where to put a copy of “{book.title}”",
        os.path.expanduser("~"))
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    archive = _unwrap(github.drafts_archive(_token(), book))
    name = f"{book.slug} (drafts, {time.strftime('%Y-%m-%d')})"
    try:
        copy = drafts.unpack_copy(archive, path, name)
    except drafts.CopyError as e:
        raise KeyError(str(e))
    copy.update(repo=book.repo, branch=book.drafts_branch)
    return copy


# --- bringing a Word document in ---------------------------------------------
#
# One import at a time, held here rather than in a Session, because it has no
# chapter and no vault yet - it is a file on the author's desk that is on its
# way to becoming one. Everything is converted into a temporary folder first;
# the vault is not touched until r_import_save.

CHAPTERS_FOLDER = "chapters"   # where a book keeps its chapters

IMPORT = {
    "docx": None,        # the Word document chosen
    "folder": None,      # where in the vault it is going
    "result": None,      # what convert.convert() produced, staged in /tmp
    "last_saved": None,  # the chapter just written, so the checks do not warn
                         # the author that "something else" has just saved it
}


def _forget_import():
    convert.discard(IMPORT.get("result"))
    IMPORT["result"] = None


def _chapters_folder():
    """The open vault's chapters folder, where a new chapter belongs. None
    when no vault is open or it has no such folder."""
    ident = WORKSPACE["vault"]
    if ident is None:
        return None
    folder = os.path.join(ident["root"], CHAPTERS_FOLDER)
    if not os.path.isdir(folder) or convert.find_vault_root(folder) != ident["root"]:
        return None
    return folder


def _chapters_in(folder):
    return sum(1 for n in os.listdir(folder)
               if n.lower().endswith((".md", ".markdown")))


def r_import_status(handler, data):
    """Whether this Mac can read Word documents at all, and where the chapter
    will go: the open vault's chapters folder, until the author picks another.
    """
    if IMPORT["folder"] is None:
        IMPORT["folder"] = _chapters_folder()
    st = convert.status()
    st["docx"] = IMPORT["docx"]
    st["docx_name"] = os.path.basename(IMPORT["docx"]) if IMPORT["docx"] else None
    st["folder"] = IMPORT["folder"]
    st["folder_name"] = (os.path.basename(IMPORT["folder"])
                         if IMPORT["folder"] else None)
    st["chapters_here"] = (_chapters_in(IMPORT["folder"])
                           if IMPORT["folder"] and os.path.isdir(IMPORT["folder"])
                           else 0)
    return st


def r_import_install(handler, data):
    """Fetch pandoc's own signed installer and hand it to Apple's installer.

    The author answers the installer's questions and comes back. They are never
    asked to type a command, here or anywhere else.
    """
    ok, message = convert.download_installer()
    if not ok:
        raise KeyError(message)
    return {"started": True, "message": message}


def r_import_pick_docx(handler, data):
    start = os.path.expanduser("~/Documents")
    if not os.path.isdir(start):
        start = os.path.expanduser("~")
    path, err = picker.choose_word_document(
        "Choose the Word document you want to bring in", start
    )
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    if not path.lower().endswith(".docx"):
        raise KeyError(
            "That is not a Word document. It needs to be a .docx file - the kind "
            "Word has saved since 2007. If yours is an older .doc, open it in "
            "Word and use File, then Save As, to save it as a .docx first."
        )
    _forget_import()
    IMPORT["docx"] = path
    return {"docx": path,
            "docx_name": os.path.basename(path),
            "suggested_name": convert.suggest_name(path),
            "size": os.path.getsize(path)}


def r_import_pick_folder(handler, data):
    ident = WORKSPACE["vault"]
    start = IMPORT["folder"] or _chapters_folder() \
        or (ident["root"] if ident else None) or os.path.expanduser("~")
    path, err = picker.choose_folder(
        "Choose where in your vault the converted chapter should go", start
    )
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    folder = path.rstrip("/")
    top = convert.find_vault_root(folder)
    if top is None:
        return {"error": convert.vault_problem(folder)}
    try:
        _open_vault(top)
    except KeyError as e:
        return {"error": str(e.args[0])}
    IMPORT["folder"] = folder
    return {"folder": folder,
            "folder_name": os.path.basename(folder) or folder,
            "chapters_here": _chapters_in(folder),
            # The top of the vault is a folder an author can pick by mistake:
            # it opens there, and chapters don't belong there.
            "at_top": os.path.abspath(folder) == os.path.abspath(top)}


def r_import_convert(handler, data):
    """Convert into a temporary folder and say what came out. Writes nothing."""
    if not IMPORT["docx"]:
        raise KeyError("Please choose a Word document first.")
    if not IMPORT["folder"]:
        raise KeyError("Please choose where the chapter should go first.")

    name = (data.get("name") or "").strip()
    if not name:
        raise KeyError("Please give the chapter a name.")
    if not name.lower().endswith((".md", ".markdown")):
        name += ".md"

    problem = convert.name_problem(name)
    if problem:
        raise KeyError(problem)
    # What stops it being saved into the folder (a chapter of that name is
    # already there, say) doesn't stop it going to the drafts area, where it
    # replaces the chapter in a commit that can be looked back at. Only when
    # neither can happen is the author stopped before the conversion, so they
    # are never told at the end of a long import that it cannot land.
    local = convert.destination_problem(IMPORT["folder"], name)
    to_drafts, drafts_reason, drafts_why = _drafts_availability()
    if local and not to_drafts:
        raise KeyError(local)

    _forget_import()
    try:
        result = convert.convert(IMPORT["docx"], name, IMPORT["folder"])
    except convert.ConversionFailed as e:
        raise KeyError(str(e))
    ident = WORKSPACE["vault"]
    result["book"] = WORKSPACE["book"]
    result["vault"] = ident["root"] if ident else None
    result["local_problem"] = local
    IMPORT["result"] = result

    found = convert.report(result)
    return {
        "name": name,
        "path": os.path.join(IMPORT["folder"], name),
        "text": result["text"],
        "media_rel": result["media_rel"],
        "media": result["media"],
        "notes": found["notes"],
        "counts": found["counts"],
        "folder": IMPORT["folder"],
        "local_problem": local,
        "drafts": {"available": to_drafts, "why": drafts_reason,
                   "message": drafts_why},
    }


def r_import_save(handler, data):
    """Write the converted chapter, and its pictures, into the vault."""
    result = IMPORT.get("result")
    if not result:
        raise KeyError("There is nothing converted to save. Please start again.")

    media_rel = result.get("media_rel")
    _guard_vault_write(
        os.path.join(IMPORT["folder"], os.path.basename(result["md_name"])),
        result.get("media_target"),
    )
    chapter_path, media_path = convert.save(result, IMPORT["folder"])
    # Kept, not cleared away: the same chapter can still be sent to the
    # drafts area. It goes when another import starts, or on cancel or quit.
    result["saved"] = chapter_path
    IMPORT["last_saved"] = chapter_path
    return {
        "chapter": chapter_path,
        "chapter_name": os.path.basename(chapter_path),
        "media": media_path,
        "media_name": media_rel,
        "folder": IMPORT["folder"],
    }


def r_import_cancel(handler, data):
    _forget_import()
    return {"ok": True}


# --- sending a Word import to the drafts area -------------------------------
#
# The same converted chapter can be saved into the folder, sent to the book's
# drafts area, or both. Sending is one commit on the drafts branch, made as the
# signed-in author, on top of the drafts branch exactly as the author was shown
# it (drafts.py). The import is converted into a vault, so only the chosen
# book, and only if the open vault is still its copy when sending, is written to.

def _drafts_availability():
    """Whether this import can go to a drafts area. (True/False, why, words).

    `why` is "ok", "unchecked", "no_vault", "not_book", "signed_out" or
    "no_access"; the last two can be put right by signing in as someone else.
    """
    ident = WORKSPACE["vault"]
    if ident is None:
        return False, "no_vault", (
            "Once you choose where in your vault the chapter goes, this says "
            "whether it can also be sent to the book's drafts area.")
    book = _current_book()
    if (ident["state"] != registry.OK or book is None
            or ident["book"].slug != book.slug):
        return False, "not_book", (
            (ident["message"] + " ") if ident["message"] else "") + (
            "So this chapter can't be sent to a book's drafts area. It can "
            "still be saved into the folder.")
    token = _token()
    if not token:
        return False, "signed_out", (
            f"You aren't signed in, so this chapter can't be sent to the drafts "
            f"area of “{book.title}”. Sign in under “Waiting for you” first if "
            "you want to. It can still be saved into your vault.")
    level = _known_access(book.slug)
    if level is None:
        try:
            if _ensure_login(token) is None:
                _check_access(token, [book])
        except KeyError as e:
            return False, "signed_out", (
                str(e.args[0]) + " It can still be saved into your vault.")
        level = _known_access(book.slug)
    who = f" ({CONSOLE['login']})" if CONSOLE["login"] else ""
    if level in ("read", "none"):
        return False, "no_access", (
            f"Your account{who} can't make changes to “{book.title}” "
            f"({book.repo}), so this chapter can't be sent to its drafts area. "
            "It can still be saved into your vault. The book's maintainer can "
            "give you access, or you can sign in with a different account.")
    where = (f"the drafts area of “{book.title}” ({book.repo}, "
             f"“{book.drafts_branch}”)")
    if level is None:
        return True, "unchecked", (
            f"It could not be checked just now whether your account can change "
            f"“{book.title}”. You can try sending it to {where}; if your account "
            "can't, you will be told and nothing will change.")
    return True, "ok", (f"Once it is converted you can send it to {where}, "
                        f"as yourself{who}. Readers don't see the drafts area.")


def r_import_drafts_status(handler, data):
    """Said on the import screen, before anything is converted."""
    ok, why, message = _drafts_availability()
    return {"available": ok, "why": why, "message": message,
            "workspace": _workspace_info()}


def _guard_drafts_send(book, result):
    """Refuse unless the open vault, read again now, is `book`'s, and is the
    vault this chapter was converted in. Raises KeyError, having sent nothing.
    """
    ident = WORKSPACE["vault"]
    if ident is None:
        raise KeyError("No vault is open, so nothing was sent. Please start "
                       "again.")
    fresh = registry.identify(ident["root"])
    if fresh["key"] != ident["key"]:
        raise KeyError(
            f"The settings in “{ident['name']}” changed after it was opened, so "
            "it can't be trusted to be the same book. Nothing was sent. Open "
            "the vault again.")
    if fresh["state"] != registry.OK:
        raise KeyError(fresh["message"] + " Nothing was sent.")
    if (fresh["book"].slug != book.slug or result.get("book") != book.slug
            or result.get("vault") != ident["root"]
            or not registry.within(ident["root"], IMPORT["folder"] or "")):
        raise KeyError(
            f"This chapter was converted for a different book or vault from "
            f"“{book.title}”, the one open now. Nothing was sent. Please "
            "convert it again.")


def _drafts_plan(token, book, result):
    """Read the drafts area and work out the commit. Stored on the result."""
    paths = drafts.repo_paths(result["vault"], IMPORT["folder"],
                              result["md_name"])
    if paths is None:
        raise KeyError("Where this chapter is going isn't inside the book's "
                       "folder, so it can't be sent. Nothing was sent.")
    chapter_path, media_dir = paths
    state = _unwrap(drafts.read(token, book, chapter_path, media_dir))
    entries = []
    if not state["refused"]:
        entries = drafts.build_tree(chapter_path,
                                    result["text"].encode("utf-8"), media_dir,
                                    drafts.pictures(result),
                                    state["on_drafts"])
    result["drafts"] = {"book": book.slug, "state": state, "entries": entries,
                        "chapter_path": chapter_path}
    shown = drafts.describe(book, state, entries, chapter_path, media_dir,
                            result["text"])
    shown["head"] = state["head"]
    return shown


def r_import_drafts_check(handler, data):
    """What sending would do. Reads the drafts area; changes nothing."""
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    result = IMPORT.get("result")
    if not result:
        raise KeyError("There is nothing converted to send. Please start again.")
    _guard_drafts_send(book, result)
    return _drafts_plan(token, book, result)


def r_import_drafts_send(handler, data):
    """One commit on the drafts branch, or a refusal that changed nothing."""
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    result = IMPORT.get("result")
    if not result:
        raise KeyError("There is nothing converted to send. Please start again.")
    _guard_drafts_send(book, result)
    plan = result.get("drafts")
    if not plan or plan["book"] != book.slug:
        raise KeyError("Please look at what will be sent again before sending.")
    state = plan["state"]
    if data.get("head") != state["head"]:
        raise KeyError("This screen is out of date, so nothing was sent. "
                       "Please look at what will be sent again.")
    if state["refused"]:
        raise KeyError(state["refused"])
    if state["exists"] and not data.get("replace"):
        raise KeyError(
            "A chapter of this name is already in the drafts area. Tick the "
            "box to say it should be replaced, or go back and give this one "
            "a different name. Nothing was sent.")

    name = os.path.basename(result["md_name"])
    message = (f"Bring in {name} from Word\n\n"
               f"Converted from “{os.path.basename(result['docx'])}” by the "
               "Authoring Assistant.")
    sent = drafts.send(token, book, state, plan["entries"], message)
    if isinstance(sent, dict) and sent.get("moved"):
        result["drafts"] = None
        shown = _drafts_plan(token, book, result)
        return {"moved": True, "message": drafts.MOVED, "drafts": shown}
    sent = _unwrap(sent)
    result["sent"] = sent["sha"]
    result["drafts"] = None
    removed = [e["path"] for e in plan["entries"] if e["data"] is None]
    return {
        "sent": True,
        "sha": sent["sha"],
        "url": sent.get("url"),
        "repo": book.repo,
        "branch": book.drafts_branch,
        "chapter_path": plan["chapter_path"],
        "files": len(plan["entries"]) - len(removed),
        "removed": len(removed),
        "saved": result.get("saved"),
    }


def r_quit(handler, data):
    _forget_import()
    threading.Timer(0.4, SHUTDOWN.set).start()
    return {"stopping": True}


# --- which book, and which vault ---------------------------------------------
#
# Everything the console shows or does belongs to one book, and the author is
# never left to wonder which. The author chooses it from the books they can act
# on, and that choice decides (BOOK-ONE-TO-QUARTZ §8 step 2): chapters are
# changed in the book's drafts area, so no vault is needed. There is at most
# one vault open for the whole app, whichever half opened it, and it can only
# be the chosen book's copy: a vault of another book is refused on opening, and
# choosing another book closes it.
#
# That rule is what makes the dangerous mix — a suggestion for one book
# written into another book's chapter — impossible to reach through the
# interface. It is not what makes it impossible. That is done at the moment of
# writing, by reading the vault's identity from disk again and refusing unless
# it is still the book the text came from (see _guard_book_write). A screen
# drawn for another book, a vault whose settings changed after it was opened,
# or a second tab all fail there, and nothing is written.

WORKSPACE = {
    "vault": None,       # registry.identify() of the open vault, or None
    "book": None,        # slug of the book being worked on, or None
    "restored": False,   # whether the last choice has been brought back yet
}


def _registry():
    """The list of books, or None. Never raises."""
    try:
        return registry.get()
    except registry.RegistryError:
        return None


def _current_book():
    """The Book being worked on, or None."""
    slug = WORKSPACE["book"]
    reg = _registry()
    if not slug or reg is None:
        return None
    return reg.find(slug)


def _forget_book_work():
    """Anything worked out for one book or vault is useless for another."""
    CONSOLE["plans"].clear()
    CONSOLE["loaded"] = None


def _set_book(slug, remember=True):
    if slug != WORKSPACE["book"]:
        _forget_book_work()
    WORKSPACE["book"] = slug
    if slug and remember:
        config.write_state(last_book=slug)


def _restore_last_book():
    """Bring back the book chosen last time, once, if nothing else decided it."""
    if WORKSPACE["restored"]:
        return
    WORKSPACE["restored"] = True
    if WORKSPACE["book"] or WORKSPACE["vault"]:
        return
    slug = config.read_state().get("last_book")
    reg = _registry()
    if not slug or reg is None or reg.find(slug) is None:
        return
    if _known_access(slug) in ("read", "none"):
        return   # no longer theirs to act on; let them choose again
    WORKSPACE["book"] = slug


def _open_vault(path):
    """Make this the app's vault. The chosen book decides the book, not it.

    Refuses — raises KeyError, and changes nothing — when the vault names a
    book and that claim does not hold up, or when it is a copy of a book other
    than the one chosen. With no book chosen, a vault that is a copy of a book
    chooses it. A folder that isn't linked to a book opens for the Chapters
    tools and leaves the book as it was. Returns the vault's identity.
    """
    ident = registry.identify(registry.vault_root(path))
    if ident["state"] in registry.FAILED_CLAIMS:
        raise KeyError(ident["message"])
    _restore_last_book()
    book = _current_book()
    if ident["state"] == registry.OK:
        if book is None:
            _set_book(ident["book"].slug)
        elif book.slug != ident["book"].slug:
            raise KeyError(
                f"“{ident['name']}” is a copy of “{ident['book'].title}”, but "
                f"the book you are working on is “{book.title}”, and the book "
                "you choose decides. Nothing was opened. Change book first if "
                f"you meant to work on “{ident['book'].title}”.")
    WORKSPACE["vault"] = ident
    CONSOLE["plans"].clear()
    return ident


def _vault_info(ident):
    if not ident:
        return None
    return {k: ident[k] for k in ("root", "name", "slug", "remote", "state",
                                  "message")}


def _account_info():
    """Which GitHub account this Mac is signed in as. `login` is None until it
    has been asked for (see r_account)."""
    signed_in = bool(_token())
    return {"signed_in": signed_in,
            "login": CONSOLE["login"] if signed_in else None,
            "name": CONSOLE["who"] if signed_in else None}


def _workspace_info():
    """Everything the page needs to say which book and vault are in use."""
    _restore_last_book()
    try:
        reg = registry.get()
        reg_info = reg.describe()
    except registry.RegistryError as e:
        reg = None
        reg_info = {"source": None, "fresh": False, "as_of": None,
                    "problem": str(e)}
    book = _current_book()
    ident = WORKSPACE["vault"]
    info = None
    if book is not None:
        info = book.describe()
        info["access"] = _known_access(book.slug) or "unknown"
    return {
        "registry": reg_info,
        "account": _account_info(),
        "book": info,
        "vault": _vault_info(ident),
        # Whether the console may change a chapter in the open vault.
        "can_write_vault": bool(ident and book and ident["state"] == registry.OK
                                and ident["book"].slug == book.slug),
    }


def _guard_book_write(book, plan):
    """Refuse unless the open vault, read again now, is `book`'s vault.

    Used before a suggestion's text is written into a chapter. The text came
    from `book`'s repository, so it may only go into `book`'s vault. Raises
    KeyError, having written nothing, when that cannot be shown to be so.
    """
    ident = WORKSPACE["vault"]
    if ident is None:
        raise KeyError(
            "No vault is open, so no chapter can be changed. Nothing was done."
        )
    fresh = registry.identify(ident["root"])
    if fresh["key"] != ident["key"]:
        raise KeyError(
            f"The settings in “{ident['name']}” changed after it was opened, so "
            "it can't be trusted to be the same book. Nothing was done. Open "
            "the vault again."
        )
    if fresh["state"] != registry.OK:
        raise KeyError(fresh["message"] + " Nothing was done.")
    if fresh["book"].slug != book.slug:
        raise KeyError(
            f"This suggestion is for “{book.title}”, but the vault that is open, "
            f"“{ident['name']}”, is “{fresh['book'].title}”. Nothing was written."
        )
    if plan.get("book") != book.slug or plan.get("vault") != ident["root"]:
        raise KeyError(
            "This change was worked out for a different book or vault. Nothing "
            "was done. Please look at the suggestion again."
        )


def _guard_vault_write(*targets):
    """Refuse unless every target is inside the open vault, and the vault is
    still what it said it was when it was opened.

    Used before the Chapters tools write. Their text comes from the vault
    itself, not from any book, so a vault that isn't linked to a book is fine;
    a vault whose claim to be a book fails is not. Raises RuntimeError.
    """
    ident = WORKSPACE["vault"]
    if ident is None:
        raise RuntimeError(
            "The vault was closed after this was opened, so nothing was saved. "
            "Please start again."
        )
    for target in targets:
        if target and not registry.within(ident["root"], target):
            raise RuntimeError(
                f"The app has moved on to a different vault (“{ident['name']}”) "
                "since this was opened, so nothing was saved. Please open the "
                "chapter again."
            )
    fresh = registry.identify(ident["root"])
    if fresh["key"] != ident["key"]:
        raise RuntimeError(
            f"The settings in “{ident['name']}” changed after it was opened, so "
            "nothing was saved. Please open it again."
        )
    if fresh["state"] in registry.FAILED_CLAIMS:
        raise RuntimeError(fresh["message"] + " Nothing was saved.")
    book = WORKSPACE["book"]
    if fresh["state"] == registry.OK and fresh["book"].slug != book:
        raise RuntimeError(
            f"“{ident['name']}” is “{fresh['book'].title}”, but the app is set "
            "to a different book. Nothing was saved."
        )


def r_workspace(handler, data):
    return _workspace_info()


def r_vault_close(handler, data):
    """Close the vault. The book stays chosen; changing chapters stops."""
    WORKSPACE["vault"] = None
    CONSOLE["plans"].clear()
    return _workspace_info()


def _leave_vault():
    """Close the vault because another book was chosen. A Word import on its
    way into it is dropped, since it was going into the book being left."""
    ident = WORKSPACE["vault"]
    if ident is not None and IMPORT["folder"] and \
            registry.within(ident["root"], IMPORT["folder"]):
        _forget_import()
        IMPORT["folder"] = None
    WORKSPACE["vault"] = None


def _known_access(slug):
    """What this Mac last learned about the author's access to a book."""
    if slug in CONSOLE["access"]:
        return CONSOLE["access"][slug]
    saved = config.read_state().get("book_access") or {}
    if CONSOLE["login"] and saved.get("login") != CONSOLE["login"]:
        return None
    return (saved.get("access") or {}).get(slug)


def _check_access(token, books):
    """Ask which of these books the author can act on. (checked, problem)."""
    problem = None
    for book in books:
        level = github.repo_access(token, book)
        if isinstance(level, github.Problem):
            if level.needs_signin:
                _unwrap(level)
            problem = level
            continue
        CONSOLE["access"][book.slug] = level
    if CONSOLE["access"]:
        saved = config.read_state().get("book_access") or {}
        access = dict(saved.get("access") or {}) \
            if saved.get("login") == CONSOLE["login"] else {}
        access.update(CONSOLE["access"])
        config.write_state(book_access={
            "login": CONSOLE["login"], "checked_at": time.time(),
            "access": access,
        })
    return problem


def _ensure_login(token):
    if CONSOLE["login"] is None:
        who = github.whoami(token)
        if isinstance(who, github.Problem):
            if who.needs_signin:
                _unwrap(who)
            return who
        CONSOLE["who"], CONSOLE["login"] = who["name"], who["login"]
    return None


def r_books(handler, data):
    """The books the signed-in author can act on.

    Checked against their own access to each book's repository, so a book they
    cannot change is never offered and then refused later.
    """
    token = _token()
    if not token:
        _needs_signin()
    registry.retry_if_unavailable()
    try:
        reg = registry.get()
    except registry.RegistryError as e:
        raise KeyError(str(e))

    books = reg.active()
    offline = False
    note = ""
    problem = _ensure_login(token)
    if problem is None:
        problem = _check_access(token, books)
    if problem is not None:
        offline = problem.offline
        saved = config.read_state().get("book_access") or {}
        when = saved.get("checked_at")
        note = (
            ("This Mac is not online, so " if offline else
             "Access could not be checked just now, so ")
            + ("this is who could make changes as of "
               + time.strftime("%-d %B %Y", time.localtime(when)) + "."
               if when else "it isn't known yet which books you can change.")
        )

    shown, hidden = [], 0
    for book in books:
        level = _known_access(book.slug)
        if level == "write":
            item = book.describe()
            item["access"] = level
            shown.append(item)
        else:
            hidden += 1
    return {
        "books": shown,
        "hidden": hidden,
        "offline": offline,
        "note": note,
        "workspace": _workspace_info(),
    }


def r_books_choose(handler, data):
    """Choose the book. This, not an open vault, decides which book it is.

    A vault that is a copy of a different book is closed, since nothing in it
    belongs to the book chosen. A folder linked to no book stays open.
    """
    slug = (data.get("slug") or "").strip()
    try:
        book = registry.get().resolve(slug)
    except registry.RegistryError as e:
        raise KeyError(str(e))
    if _known_access(book.slug) != "write":
        raise KeyError(
            f"Your account can't make changes to “{book.title}” ({book.repo}), "
            "so it can't be chosen here."
        )
    ident = WORKSPACE["vault"]
    if ident is not None and ident["slug"] and ident["slug"] != book.slug:
        _leave_vault()
    _set_book(book.slug)
    return _workspace_info()


# --- the console -------------------------------------------------------------
#
# Everything below serves the second half of the app: the readable layer over the
# place the textbook is stored. The author never sees the vocabulary of that
# service, and never has to open it.

CONSOLE = {
    "who": None,      # who is signed in, remembered so we don't ask every time
    "login": None,    # their account name, which the access record is kept under
    "signin": None,   # the sign-in attempt in progress
    "leaving": None,  # the account signed out of to use a different one
    "access": {},     # book slug -> "write", "read" or "none", checked this run
    "loaded": None,   # the suggestions last shown: {"slug", "suggestions"}
    "plans": {},      # (book slug, suggestion number) -> the change worked out
}


def _client_id():
    """The sign-in identifier: one pasted in Settings, else the registry's."""
    saved = (config.read_state().get("github_client_id") or "").strip()
    if saved:
        return saved
    reg = _registry()
    return reg.client_id if reg is not None else ""


def _token():
    return keychain.load(keychain.ACCOUNT_GITHUB)


def _needs_signin():
    raise KeyError("You are not signed in. Open the console and press Sign in.")


def _unwrap(result):
    """Turn a Problem into the error the interface shows, or pass the data on."""
    if isinstance(result, github.Problem):
        if result.needs_signin:
            CONSOLE["who"] = None
            CONSOLE["login"] = None
        raise KeyError(result.message)
    return result


def _book_for(data):
    """The book a console request is about, and only if it is the current one.

    The page says which book it was showing. A page drawn for one book must
    never act on another, so a disagreement stops the request here.
    """
    book = _current_book()
    if book is None:
        raise KeyError(
            "No book is chosen, so nothing was done. Choose one at the top of "
            "the page."
        )
    shown = data.get("book")
    if shown != book.slug:
        raise KeyError(
            "This screen was drawn for a different book from the one the app is "
            f"now working on (“{book.title}”), so nothing was done. Press "
            "“Check again”."
        )
    return book


def _require_write(book):
    if _known_access(book.slug) in ("read", "none"):
        raise KeyError(
            f"Your account can't make changes to “{book.title}” ({book.repo}), "
            "so nothing was done. The book's maintainer can give you access."
        )


def r_console_status(handler, data):
    """Enough for the console to decide what to draw.

    Makes no calls to the service behind the console. The list of books is
    fetched once per run, the first time anything needs it.
    """
    client = _client_id()
    return {
        "configured": bool(client),
        "client_hint": client[:8] if client else "",
        "signed_in": bool(_token()),
        "who": CONSOLE["who"],
        "login": CONSOLE["login"],
        "keychain": keychain.available(),
        "workspace": _workspace_info(),
    }


def r_console_save_client(handler, data):
    """The sign-in identifier. Not a secret; it ships with the app elsewhere."""
    value = (data.get("client_id") or "").strip()
    if not value:
        raise KeyError("Please paste the sign-in identifier first.")
    config.write_state(github_client_id=value)
    return {"saved": True, "configured": True}


def r_console_signin_start(handler, data):
    result = _unwrap(github.start_signin(_client_id()))
    CONSOLE["signin"] = {
        "device_code": result["device_code"],
        "interval": max(2, int(result["interval"])),
        "deadline": time.time() + int(result["expires_in"]),
    }
    return {
        "code": result["user_code"],
        "url": result["verification_uri"],
        "minutes": max(1, int(result["expires_in"]) // 60),
    }


def r_console_signin_poll(handler, data):
    attempt = CONSOLE.get("signin")
    if not attempt:
        raise KeyError("Sign-in was not started. Please press Sign in again.")
    if time.time() > attempt["deadline"]:
        CONSOLE["signin"] = None
        raise KeyError("That code ran out before it was used. Please start again.")

    result = github.poll_signin(_client_id(), attempt["device_code"])
    if isinstance(result, github.Problem):
        CONSOLE["signin"] = None
        raise KeyError(result.message)
    if result.get("waiting"):
        return {"waiting": True, "wait": attempt["interval"]}
    if result.get("slow_down"):
        attempt["interval"] = max(attempt["interval"], int(result["slow_down"]))
        return {"waiting": True, "wait": attempt["interval"]}

    token = result["token"]
    if not keychain.save(keychain.ACCOUNT_GITHUB, token):
        raise KeyError(
            "Signing in worked, but the token could not be stored in this Mac's "
            "Keychain, so it was not kept. Nothing was changed. Try again, and "
            "allow the Keychain prompt if one appears."
        )
    CONSOLE["signin"] = None
    CONSOLE["access"] = {}
    who = github.whoami(token)
    if isinstance(who, github.Problem):
        CONSOLE["who"] = None
        CONSOLE["login"] = None
        raise KeyError(who.message)
    CONSOLE["who"], CONSOLE["login"] = who["name"], who["login"]
    leaving, CONSOLE["leaving"] = CONSOLE.get("leaving"), None
    # GitHub signs in whoever its web page is signed in as, so asking for a
    # different account can quietly give back the same one.
    same = bool(leaving) and leaving.casefold() == who["login"].casefold()
    return {"signed_in": True, "who": who["name"], "login": who["login"],
            "same_account": same}


def r_console_signout(handler, data):
    """Forget this Mac's sign-in, for real, not just on screen.

    The token is taken out of the Keychain and then read back: the author is
    only told they are signed out once it can't be. `switching` says they want
    a different account, so the next sign-in can tell them if GitHub hands back
    the same one.
    """
    leaving = CONSOLE["login"]
    gone = keychain.forget(keychain.ACCOUNT_GITHUB)
    CONSOLE["who"] = None
    CONSOLE["login"] = None
    CONSOLE["signin"] = None
    CONSOLE["access"] = {}
    _forget_book_work()
    # What one account could change says nothing about the next one.
    config.write_state(book_access=None)
    if IMPORT.get("result"):
        IMPORT["result"]["drafts"] = None
    if not gone:
        CONSOLE["leaving"] = None
        raise KeyError(
            "The sign-in could not be taken out of this Mac's Keychain, so you "
            "are still signed in. Try again, and allow the Keychain prompt if "
            "one appears.")
    CONSOLE["leaving"] = leaving if data.get("switching") else None
    return {"signed_in": False, "was": leaving, "workspace": _workspace_info()}


def r_account(handler, data):
    """Who is signed in, found out if it isn't known yet. For the book band."""
    token = _token()
    problem = None
    if token:
        try:
            found = _ensure_login(token)
        except KeyError as e:        # the sign-in is no longer accepted
            found = None
            problem = str(e.args[0])
        if found is not None:
            problem = found.message
    info = _workspace_info()
    info["account_problem"] = problem
    return info


def r_console_pick_vault(handler, data):
    ident = WORKSPACE["vault"]
    start = ident["root"] if ident else os.path.expanduser("~")
    path, err = picker.choose_folder(
        "Choose your vault folder (the folder holding your chapters)", start
    )
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    try:
        _open_vault(path.rstrip("/"))
    except KeyError as e:
        return {"error": str(e.args[0])}
    return {"workspace": _workspace_info()}


def r_console_load(handler, data):
    """Everything waiting for one book, in one call.

    Each part fails on its own: one thing being unavailable never blanks the
    rest of the screen.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)

    out = {"suggestions": [], "drafts": [], "weekly": [], "publish": None,
           "problems": [], "offline": False, "book": book.describe()}

    who = _ensure_login(token)
    if who is not None:
        out["problems"].append(who.message)
        out["offline"] = who.offline
    out["who"] = CONSOLE["who"]

    if book.slug not in CONSOLE["access"]:
        _check_access(token, [book])
    out["access"] = _known_access(book.slug) or "unknown"

    issues = github.suggested_edits(token, book)
    if isinstance(issues, github.Problem):
        out["problems"].append("Suggestions: " + issues.message)
        out["offline"] = out["offline"] or issues.offline
    else:
        mine = [i for i in issues if github.from_book(i, book)]
        if len(mine) != len(issues):
            out["problems"].append(
                "Suggestions: some came back from somewhere other than "
                f"{book.repo} and were left out.")
        out["suggestions"] = [console.parse_suggestion(i) for i in mine]
        CONSOLE["loaded"] = {
            "slug": book.slug,
            "suggestions": {str(s["number"]): s for s in out["suggestions"]},
        }

    prs = github.draft_changes(token, book)
    if isinstance(prs, github.Problem):
        out["problems"].append("Draft changes: " + prs.message)
        out["offline"] = out["offline"] or prs.offline
    else:
        out["drafts"] = [console.describe_change(p) for p in prs
                         if github.from_book(p, book)]

    publish, publish_problem = _publish_state(token, book)
    if publish_problem is not None:
        out["problems"].append("Going live: " + publish_problem.message)
        out["offline"] = out["offline"] or publish_problem.offline
    else:
        out["publish"] = publish

    weekly = github.weekly_status(token, book)
    if isinstance(weekly, github.Problem):
        out["problems"].append("Weekly jobs: " + weekly.message)
        out["offline"] = out["offline"] or weekly.offline
    else:
        out["weekly"] = weekly

    out["workspace"] = _workspace_info()
    return out


def _loaded_suggestion(book, number):
    """The suggestion as this app fetched it for this book, never as the page
    sends it back."""
    loaded = CONSOLE["loaded"] or {}
    suggestion = (loaded.get("suggestions") or {}).get(str(number))
    if loaded.get("slug") != book.slug or suggestion is None:
        raise KeyError("That suggestion isn't in the list any more. Press "
                       "“Check again”.")
    return suggestion


def _plan_on_drafts(token, book, suggestion):
    """What accepting would change in the chapter as the drafts area holds it.

    The page is named by whoever filed the suggestion, so a name that isn't a
    plain path inside the book is never followed.
    """
    path = suggestion["path"]
    text, snap, problem = None, None, None
    if console.literal_replacement(suggestion["suggestion"] or ""):
        # The chapter is asked for alongside the listing of drafts, not after
        # it: one round trip to the service rather than four in a row.
        if not token:
            read = github.Problem("You aren't signed in.")
        elif drafts.safe_path(path):
            read = drafts.snapshot_with(token, book, path)
        else:
            read = drafts.snapshot(token, book)
            read = read if isinstance(read, github.Problem) else (read, None)
        if isinstance(read, github.Problem):
            problem = read
        else:
            snap, data = read
            text = None if data is None else drafts.text_of(data)
    plan = console.plan_in_text(text, path, suggestion["suggestion"])
    if problem is not None:
        plan["reason"] = ("The drafts area couldn't be read just now, so the "
                          "tool can't make this change there: " + problem.message)
    elif snap is not None and not drafts.safe_path(path):
        plan["reason"] = ("This suggestion names a page outside the book, so "
                          "the tool will not touch it.")
    # Named afresh every time, so that an acceptance can show which plan the
    # author was looking at, and that they were looking at one at all.
    plan.update(book=book.slug, path=path, snap=snap,
                head=snap["head"] if snap else None,
                id=secrets.token_urlsafe(12))
    return plan


def _shown_plan(book, plan):
    """What the page is told about a plan."""
    vault = plan.get("vault_plan")
    ident = WORKSPACE["vault"]
    return {
        "plan_id": plan["id"],
        "can_apply": plan["can_apply"],
        "reason": plan["reason"],
        "line_no": plan["line_no"],
        "before": plan["before"],
        "after": plan["after"],
        "head": plan["head"],
        "branch": book.drafts_branch,
        "vault": None if vault is None else {
            "name": ident["name"] if ident else "",
            "can_apply": vault["can_apply"],
            "reason": vault["reason"],
            # Ticked to start with while readers see what is published from
            # the author's folder.
            "suggested": book.from_folder,
        },
    }


def r_console_plan(handler, data):
    """Work out, without changing anything, what accepting would do.

    The change goes to the chapter in the drafts area, whether or not a vault
    is open. While a vault of this book is open, the same change can be made
    in it too: until the book moves (BOOK-ONE-TO-QUARTZ §8 step 16) a book
    still published from the author's folder shows readers what is there.
    """
    book = _book_for(data)
    number = str(data.get("number"))
    suggestion = _loaded_suggestion(book, number)

    plan = _plan_on_drafts(_token(), book, suggestion)
    ident = WORKSPACE["vault"]
    if ident is not None and _workspace_info()["can_write_vault"]:
        vault = console.plan_change(ident["root"], suggestion["path"],
                                    suggestion["suggestion"])
        vault["book"] = book.slug
        plan["vault_plan"] = vault
    CONSOLE["plans"][(book.slug, number)] = plan
    return _shown_plan(book, plan)


def r_console_accept(handler, data):
    """Accept a suggestion: optionally make the change, then reply and close.

    The chapter is written first. If the reply or the closing then fails, the
    author is told plainly that the text is fixed but the suggestion is still
    open — which is recoverable — rather than the other way round.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)

    number = data.get("number")
    apply_it = bool(data.get("apply"))
    apply_vault = bool(data.get("apply_vault"))
    steps = []
    sent = None

    # Every acceptance answers a plan the page was shown, even one that
    # changes nothing. Without that, an Accept pressed while the plan was
    # still on its way arrives as "no change" and the reader is told the
    # author will do by hand what the tool could have done.
    plan = CONSOLE["plans"].get((book.slug, str(number)))
    if not plan or not data.get("plan_id") or data["plan_id"] != plan.get("id"):
        raise KeyError("Please look at the change again before accepting it.")
    vault_plan = plan.get("vault_plan") if plan else None
    if apply_vault:
        if not vault_plan:
            raise KeyError("Please look at the change again before accepting it.")
        _guard_book_write(book, vault_plan)

    if apply_it:
        if data.get("head") != plan["head"]:
            raise KeyError("This screen is out of date, so nothing was done. "
                           "Please look at the suggestion again.")
        new_text, why = console.change_text(plan)
        if new_text is None:
            raise KeyError(why)
        entries = drafts.edit_entries(
            plan["snap"], {plan["path"]: new_text.encode("utf-8")})
        suggestion = _loaded_suggestion(book, number)
        message = (f"Accept suggestion #{number} on {console._page_name(plan['path'])}"
                   f"\n\nSuggested by {suggestion['who']} in #{number}. Made "
                   "with the Authoring Assistant.")
        sent = drafts.send(token, book, plan["snap"], entries, message)
        if isinstance(sent, dict) and sent.get("moved"):
            fresh = _plan_on_drafts(token, book, suggestion)
            fresh["vault_plan"] = vault_plan
            CONSOLE["plans"][(book.slug, str(number))] = fresh
            return {"moved": True, "message": drafts.MOVED_SUGGESTION,
                    "plan": _shown_plan(book, fresh)}
        sent = _unwrap(sent)
        steps.append(f"Line {plan['line_no']} was changed in the drafts area, "
                     "as one change of its own.")

    if apply_vault:
        ok, message = console.apply_change(vault_plan)
        if not ok:
            raise RuntimeError(
                ("The chapter was changed in the drafts area, but not in your "
                 "vault: " if sent else "") + message +
                " The suggestion is still open.")
        steps.append(message.replace("was updated", "was updated in your vault"))

    commit_url = ""
    if sent:
        commit_url = sent.get("url") or github.commit_page(book, sent.get("sha"))
    by_hand = not sent and not apply_vault
    if by_hand and _already_accepted(book, number):
        raise KeyError("You have already accepted this one, and the reader "
                       "has been thanked. Once the change is in the drafts "
                       "area, press “I've made the change”.")
    reply = console.reply_on_accept(commit_url, vault_changed=apply_vault)
    done = ("The chapter was changed in the drafts area. " if sent else
            "The chapter was changed in your vault. " if apply_vault else "")
    failed, problem = _reply_and_close(token, book, number, reply, commit_url,
                                       keep_open=by_hand)
    if failed == "reply":
        raise RuntimeError(done + "The thank-you could not be sent: " +
                           problem.message + " The suggestion is still open.")
    if sent:
        steps.append("A thank-you was sent, with a link to the change.")
    elif apply_vault:
        steps.append("A thank-you was sent, saying the change was made in your "
                     "copy of the book and reaches readers when it is next "
                     "published.")
    else:
        steps.append("A thank-you was sent, saying you will make the change by "
                     "hand. The chapter itself was not changed — make the "
                     "change under Chapters.")
    if failed == "close":
        raise RuntimeError(done + "The thank-you was sent, but the suggestion "
                           "could not be marked as dealt with: " +
                           problem.message)
    if failed == "label":
        raise RuntimeError("The thank-you was sent, but the suggestion could "
                           "not be marked as accepted: " + problem.message +
                           " It is still open, under Waiting for you.")
    if by_hand:
        steps.append("The suggestion stays under Waiting for you, marked "
                     "Accepted, until the change is made. Once it is in the "
                     "drafts area, open it and press “I've made the change”.")
    else:
        steps.append("The suggestion was marked as dealt with.")
    CONSOLE["plans"].pop((book.slug, str(number)), None)
    out = {"done": True, "steps": steps, "kept_open": by_hand}
    if sent:
        out.update(sha=sent["sha"], url=commit_url, branch=book.drafts_branch)
    return out


def _already_accepted(book, number):
    """True if the suggestion, as this app last fetched it, was already
    accepted and left open for the author to make by hand."""
    return bool(_loaded_suggestion(book, number).get("accepted"))


def _reply_and_close(token, book, number, reply, commit_url="",
                     keep_open=False):
    """Answer a suggestion and close it: the only way the console closes one,
    and the only way it answers one.

    The rule it holds every reply to: the reader is told the chapter changed
    only when a commit holds the change, and the reply links that commit. A
    reply that says otherwise is not sent, and the suggestion stays open.

    `keep_open` is for a suggestion accepted but not yet made, because the
    tool couldn't make it: it is labelled accepted and left open, so that it
    stays on the author's list until a commit holds the change.

    Returns (None, None), or ("reply" | "close" | "label", the problem) for
    the step that failed. A failed reply leaves the suggestion open.
    """
    if console.claims_change(reply) and not (commit_url and commit_url in reply):
        raise RuntimeError(
            "Nothing was sent: the reply would have told the reader the chapter "
            "was changed, but there is no change to link to. The suggestion is "
            "still open.")
    replied = github.comment(token, book, number, reply)
    if isinstance(replied, github.Problem):
        return "reply", replied
    github.drop_label(token, book, number, console.NEEDS_TRIAGE)
    if keep_open:
        labelled = github.add_label(token, book, number, console.ACCEPTED)
        if isinstance(labelled, github.Problem):
            return "label", labelled
        _mark_loaded_accepted(book, number)
        return None, None
    closed = github.close_issue(token, book, number)
    if isinstance(closed, github.Problem):
        return "close", closed
    return None, None


def _mark_loaded_accepted(book, number):
    loaded = CONSOLE["loaded"] or {}
    if loaded.get("slug") == book.slug:
        s = (loaded.get("suggestions") or {}).get(str(number))
        if s is not None:
            s["accepted"] = True


def r_console_made(handler, data):
    """Close a suggestion accepted by hand, once a commit holds the change.

    The tool can't see whether the change the author made is the one the
    reader asked for; the author says so. What it can see is whether anything
    has changed the page in the drafts area since the suggestion was
    accepted, and it closes only with a link to that commit. Asked with no
    `sha`, it names the commit it would link, and changes nothing; asked with
    the `sha` it named, it replies and closes.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    number = data.get("number")
    suggestion = _loaded_suggestion(book, number)
    if not suggestion.get("accepted"):
        raise KeyError("This suggestion hasn't been accepted yet.")
    path = suggestion["path"]
    if not drafts.safe_path(path):
        raise KeyError("This suggestion names a page outside the book, so "
                       "there is no change to link it to. Reply to it on "
                       "the website instead.")
    since = _unwrap(github.labelled_at(token, book, number, console.ACCEPTED))
    changes = [] if since is None else _unwrap(
        github.changes_since(token, book, path, since))
    if not changes:
        raise KeyError(
            f"Nothing has changed {suggestion['page']} in the drafts area since "
            "you accepted this, so there is no change to show the reader yet. "
            "Make the change under Chapters and send it to drafts, then press "
            "this again.")
    newest = changes[0]
    if not data.get("sha"):
        return {"change": newest, "page": suggestion["page"]}
    if data["sha"] != newest["sha"]:
        raise KeyError("The page has changed again since you looked, so "
                       "nothing was sent. Please look again.")
    reply = console.thanks_with_change(newest["url"])
    failed, problem = _reply_and_close(token, book, number, reply, newest["url"])
    if failed == "reply":
        raise RuntimeError("The thank-you could not be sent: " +
                           problem.message + " The suggestion is still open.")
    if failed == "close":
        raise RuntimeError("The thank-you was sent, but the suggestion could "
                           "not be marked as dealt with: " + problem.message)
    return {"done": True, "steps": [
        "A thank-you was sent, with a link to your change.",
        "The suggestion was marked as dealt with."]}


def r_console_decline(handler, data):
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    number = data.get("number")

    failed, problem = _reply_and_close(token, book, number, console.DECLINED)
    if failed == "reply":
        raise RuntimeError("The reply could not be sent: " + problem.message)
    if failed == "close":
        raise RuntimeError(
            "The reply was sent, but the suggestion could not be marked as "
            "dealt with: " + problem.message
        )
    return {"done": True, "steps": ["A polite reply was sent.",
                                    "The suggestion was closed."]}


# --- the one door from the drafts area to the live book ----------------------
#
# Accepting a change folds it into the drafts area. That is not the live book,
# and on its own it reaches no reader: the author's vault tracks the live book,
# and nothing moves the drafts across. So accepting also puts the drafts in line
# for the live book, by opening — or refreshing — the single pull request that
# carries them.
#
# It stops there deliberately. It does not merge that request itself. The drafts
# area is shared: it holds whatever has been published from the browser editor as
# well as whatever has been accepted here, so merging it would publish other
# people's unreviewed work on the strength of one press about one change. The
# live book is also protected and has checks of its own, and the author's vault
# tracks it, so writing to it behind the author's back would leave that copy
# silently out of date. Publishing is therefore its own deliberate press, on its own
# screen, showing what would go — the same reasoning that keeps the weekly
# generated pull requests out of this app.


def _publish_state(token, book, tries=1):
    """What stands between the drafts area and the live book.

    Reads; never opens or changes anything. Returns (info, problem). `info` is
    None when the live book already has everything the drafts area holds.
    """
    compare = github.drafts_ahead_of_live(token, book)
    if isinstance(compare, github.Problem):
        return None, compare
    existing = github.open_publish_request(token, book)
    if isinstance(existing, github.Problem):
        return None, existing

    if existing is None:
        if int(compare.get("ahead_by") or 0) <= 0:
            return None, None
        # Something is waiting with no way through. Said out loud rather than
        # left as a silently shut door.
        files = [f for f in (compare.get("files") or []) if isinstance(f, dict)]
        return {
            "open": False,
            "waiting": True,
            "number": None,
            "url": "",
            "page_count": len(files),
            "pages": [],
            "change_count": int(compare.get("ahead_by") or 0),
            "who": [],
            "state": "not_open",
            "state_words": console.PUBLISH_NOT_OPEN,
            "can_publish": False,
        }, None

    state = github.mergeability(token, book, existing.get("number"), tries=tries)
    info = console.describe_publish(existing, compare, state, book)
    info["open"] = True
    info["waiting"] = False
    return info, None


def _open_or_refresh_publish_request(token, book):
    """Make sure the one publish request is open and says what the drafts area
    now holds. Returns (info, problem).

    There is only ever one, and its description is rewritten rather than added
    to, because what it carries is the drafts area as it stands — including
    anything published from the browser editor — and not the change just accepted.
    """
    compare = github.drafts_ahead_of_live(token, book)
    if isinstance(compare, github.Problem):
        return None, compare
    existing = github.open_publish_request(token, book)
    if isinstance(existing, github.Problem):
        return None, existing

    if existing is None and int(compare.get("ahead_by") or 0) <= 0:
        return None, None

    body = console.publish_request_body(compare)
    if existing is not None:
        pr = github.update_publish_request(
            token, book, existing.get("number"), console.PUBLISH_TITLE, body)
        if isinstance(pr, github.Problem):
            # The request is open and carries the change whatever happens to
            # its description; only the description is now out of date, which
            # is not worth telling the author the accept failed over.
            pr = existing
        opened = False
    else:
        pr = github.create_publish_request(token, book, console.PUBLISH_TITLE,
                                           body)
        opened = True
        if isinstance(pr, github.Problem):
            # Another copy of the app, or the author on the web, may have opened
            # one in the moment between looking and asking.
            again = github.open_publish_request(token, book)
            if isinstance(again, github.Problem) or again is None:
                return None, pr
            pr, opened = again, False

    # Just opened or just changed, so the answer is not ready yet; wait a little
    # rather than report "not known" when a moment would settle it.
    state = github.mergeability(token, book, pr.get("number"), tries=4)
    info = console.describe_publish(pr, compare, state, book)
    info["open"] = True
    info["waiting"] = False
    info["opened"] = opened
    return info, None


def r_console_draft_detail(handler, data):
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    files = github.change_files(token, book, data.get("number"))
    if isinstance(files, github.Problem):
        raise KeyError(files.message)
    return console.readable_change(files)


def r_console_draft_accept(handler, data):
    """Accept a proposed change, and put it in line for the live book.

    Two things happen, in this order, and they are reported separately: the
    change is folded into the drafts area, and the drafts area is put in line to
    go to readers. If the first works and the second does not, the author is
    told exactly that — the change is safe, nothing has reached readers, and it
    can be tried again — rather than one sentence that hides which half failed.

    Publishing is not done here. See the note above _publish_state.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    number = data.get("number")
    title = data.get("title") or "Accepted draft change"

    result = github.accept_change(token, book, number, title)
    if isinstance(result, github.Problem):
        raise RuntimeError(result.message)

    steps = ["The change was folded into the drafts area."]

    info, problem = _open_or_refresh_publish_request(token, book)
    if problem is not None:
        return {
            "done": True,
            "steps": steps,
            "publish": None,
            "warning": (
                "The change is safely in the drafts area, but it could not be "
                "put in line for the live book: " + problem.message +
                " Nothing has reached readers, and nothing was lost. Go back "
                "and press “Put it in line” to try again."
            ),
        }

    if info is None:
        steps.append("The live book already has this, so there is nothing "
                     "waiting to go to readers.")
        return {"done": True, "steps": steps, "publish": None, "warning": ""}

    steps.append(
        "It was put in line for the live book, along with everything else "
        "waiting in the drafts area."
        if info.get("opened") else
        "It joined the changes already in line for the live book."
    )
    steps.append("Nothing has reached readers yet. Publishing is a separate "
                 "press, under “Going live” on the previous screen.")

    warning = ""
    if info.get("state") == "conflict":
        warning = info.get("state_words", "")
    else:
        steps.append(info.get("state_words", ""))
    return {"done": True, "steps": steps, "publish": info, "warning": warning}


def r_console_publish_prepare(handler, data):
    """Put whatever is in the drafts area in line for the live book.

    Opens the publish request, or refreshes its description if one is already
    open. Publishes nothing.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    info, problem = _open_or_refresh_publish_request(token, book)
    if problem is not None:
        raise RuntimeError(problem.message)
    return {"publish": info}


def r_console_publish(handler, data):
    """Send the drafts to the live book.

    Only ever reached by a deliberate press with the box ticked, and only when
    the change was shown to be publishable as it stands.
    """
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    try:
        number = int(data.get("number") or 0)
    except (TypeError, ValueError):
        number = 0
    if not number:
        raise KeyError("There is nothing in line for the live book just now.")
    if not data.get("confirm"):
        raise KeyError("Please tick the box to confirm before publishing.")

    # Only ever the one request the author was shown. A screen left open while
    # the drafts moved on must not merge something else into the live book.
    existing = github.open_publish_request(token, book)
    if isinstance(existing, github.Problem):
        raise RuntimeError(existing.message)
    if existing is None or int(existing.get("number") or 0) != number:
        raise KeyError(
            "What is waiting to go live has changed since this screen was "
            "drawn, so nothing was published. Press “Check again” and look at "
            "it before publishing."
        )

    result = github.publish(token, book, number, console.PUBLISH_TITLE)
    if isinstance(result, github.Problem):
        raise RuntimeError(result.message)
    if not result.get("merged"):
        raise RuntimeError(
            "The service did not confirm that this was published, so it may "
            "not have been. Press “Check again” and look before trying once "
            "more."
        )
    return {"done": True, "steps": console.published_steps(book)}


def r_console_draft_decline(handler, data):
    token = _token()
    if not token:
        _needs_signin()
    book = _book_for(data)
    _require_write(book)
    result = github.close_change(token, book, data.get("number"))
    if isinstance(result, github.Problem):
        raise RuntimeError(result.message)
    return {"done": True, "steps": ["The draft change was closed."]}


ROUTES = {
    "/api/env": r_env,
    "/api/welcome-done": r_welcome_done,
    "/api/test-key": r_test_key,
    "/api/clear-key": r_clear_key,
    "/api/ping": r_ping,
    "/api/bye": r_bye,
    "/api/pick": r_pick,
    "/api/open": r_open,
    "/api/prepare": r_prepare,
    "/api/analyse": r_analyse,
    "/api/preview": r_preview,
    "/api/commit": r_commit,
    "/api/drafts/open": r_drafts_open,
    "/api/drafts/download": r_drafts_download,
    "/api/save-key": r_savekey,
    "/api/quit": r_quit,

    "/api/import/status": r_import_status,
    "/api/import/install": r_import_install,
    "/api/import/pick-docx": r_import_pick_docx,
    "/api/import/pick-folder": r_import_pick_folder,
    "/api/import/convert": r_import_convert,
    "/api/import/save": r_import_save,
    "/api/import/cancel": r_import_cancel,
    "/api/import/drafts-status": r_import_drafts_status,
    "/api/import/drafts-check": r_import_drafts_check,
    "/api/import/drafts-send": r_import_drafts_send,

    "/api/workspace": r_workspace,
    "/api/vault/close": r_vault_close,
    "/api/books": r_books,
    "/api/books/choose": r_books_choose,
    "/api/account": r_account,

    "/api/console/status": r_console_status,
    "/api/console/save-client": r_console_save_client,
    "/api/console/signin-start": r_console_signin_start,
    "/api/console/signin-poll": r_console_signin_poll,
    "/api/console/signout": r_console_signout,
    "/api/console/pick-vault": r_console_pick_vault,
    "/api/console/load": r_console_load,
    "/api/console/plan": r_console_plan,
    "/api/console/accept": r_console_accept,
    "/api/console/decline": r_console_decline,
    "/api/console/made": r_console_made,
    "/api/console/draft-detail": r_console_draft_detail,
    "/api/console/draft-accept": r_console_draft_accept,
    "/api/console/draft-decline": r_console_draft_decline,
    "/api/console/publish-prepare": r_console_publish_prepare,
    "/api/console/publish": r_console_publish,
}


def _watchdog():
    """Shut down when the page stops checking in, so nothing is left running."""
    while not SHUTDOWN.wait(2.0):
        now = time.time()
        closing = LIFE["closing_at"]
        if closing and now > closing:
            return SHUTDOWN.set()
        if LIFE["last_ping"] is None:
            if now - LIFE["started"] > GRACE_BEFORE_FIRST_PING:
                return SHUTDOWN.set()
        elif now - LIFE["last_ping"] > IDLE_AFTER_LAST_PING:
            return SHUTDOWN.set()


def main(argv=None):
    """Start serving, or hand over to the copy that is already running.

    There is no window and no Terminal. The only thing the author sees is the
    browser opening.
    """
    argv = argv if argv is not None else sys.argv[1:]
    quiet = "--no-browser" in argv or os.environ.get("AA_NO_BROWSER")

    if config.bundled():
        config.redirect_output()

    # A second double-click should bring the page back, not start a rival copy.
    existing = config.running_elsewhere()
    if existing:
        if not quiet:
            webbrowser.open(existing)
        print(f"Already running; reopened {existing}")
        return 0

    port = free_port()
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    config.write_runtime(port, TOKEN)
    print(f"Authoring Assistant listening on {url}")

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # Fetch the list of books now, so the first screen that needs it is not
    # the one that waits for it.
    threading.Thread(target=_registry, daemon=True).start()
    threading.Thread(target=_watchdog, daemon=True).start()
    if not quiet:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        while not SHUTDOWN.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        config.clear_runtime()
        _forget_import()   # a conversion abandoned half way leaves nothing behind
    httpd.shutdown()
    print("Authoring Assistant stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
