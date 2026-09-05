"""
The small local web server behind the interface.

It listens only on this Mac (127.0.0.1), never on the network, and every request
must carry the one-off key printed into the address the browser opens with. That
keeps other programs and web pages on the machine from reaching it.
"""

import json
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, console, convert, github, keychain, llm, picker
from .session import Session

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
        self._send(200, data, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):
        route = self.path.split("?")[0]
        if not self._authorised(allow_query=route in QUERY_TOKEN_ROUTES):
            return self._send(403, {"error": "Not allowed."})
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
        "version": config.bundle_version(),
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

    sid = secrets.token_urlsafe(12)
    with LOCK:
        SESSIONS[sid] = session
    return {
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
    if not chapter or not os.path.isfile(chapter):
        raise KeyError("That chapter could not be found. Please start again.")
    session.load_chapter(chapter)
    warnings, blockers, wrapped = session.preflight(
        we_just_wrote_it=(chapter == IMPORT.get("last_saved"))
    )
    from .terms import discover_concept_pages
    pages, source = discover_concept_pages(session.root, chapter)
    return {
        "chapter": chapter,
        "chapter_name": os.path.basename(chapter),
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
    result = session.commit(data.get("accepted", []), data.get("expand_groups", []))
    return result


def r_savekey(handler, data):
    key = (data.get("key") or "").strip()
    if not key:
        raise KeyError("Please paste a key first.")
    where = llm.save_key(key)
    return {"saved": True, "where": where, "hint": llm.key_hint(),
            "deepseek": llm.have_key()}


# --- bringing a Word document in ---------------------------------------------
#
# One import at a time, held here rather than in a Session, because it has no
# chapter and no vault yet - it is a file on the author's desk that is on its
# way to becoming one. Everything is converted into a temporary folder first;
# the vault is not touched until r_import_save.

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


def r_import_status(handler, data):
    """Whether this Mac can read Word documents at all."""
    st = convert.status()
    st["docx"] = IMPORT["docx"]
    st["docx_name"] = os.path.basename(IMPORT["docx"]) if IMPORT["docx"] else None
    st["folder"] = IMPORT["folder"]
    st["folder_name"] = (os.path.basename(IMPORT["folder"])
                         if IMPORT["folder"] else None)
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
    start = IMPORT["folder"] or CONSOLE["root"] or os.path.expanduser("~")
    path, err = picker.choose_folder(
        "Choose where in your vault the converted chapter should go", start
    )
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    folder = path.rstrip("/")
    if convert.find_vault_root(folder) is None:
        return {"error": convert.vault_problem(folder)}
    IMPORT["folder"] = folder
    return {"folder": folder,
            "folder_name": os.path.basename(folder) or folder,
            "chapters_here": sum(1 for n in os.listdir(folder)
                                 if n.lower().endswith((".md", ".markdown")))}


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

    problem = convert.destination_problem(IMPORT["folder"], name)
    if problem:
        raise KeyError(problem)

    _forget_import()
    try:
        result = convert.convert(IMPORT["docx"], name, IMPORT["folder"])
    except convert.ConversionFailed as e:
        raise KeyError(str(e))
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
    }


def r_import_save(handler, data):
    """Write the converted chapter, and its pictures, into the vault."""
    result = IMPORT.get("result")
    if not result:
        raise KeyError("There is nothing converted to save. Please start again.")

    media_rel = result.get("media_rel")
    chapter_path, media_path = convert.save(result, IMPORT["folder"])
    _forget_import()
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


def r_quit(handler, data):
    threading.Timer(0.4, SHUTDOWN.set).start()
    return {"stopping": True}


# --- the console -------------------------------------------------------------
#
# Everything below serves the second half of the app: the readable layer over the
# place the textbook is stored. The author never sees the vocabulary of that
# service, and never has to open it.

CONSOLE = {
    "root": None,     # the vault folder, needed only to apply a change
    "who": None,      # who is signed in, remembered so we don't ask every time
    "signin": None,   # the sign-in attempt in progress
    "plans": {},      # suggestion number -> the change worked out for it
}


def _client_id():
    return (config.read_state().get("github_client_id") or "").strip()


def _token():
    return keychain.load(keychain.ACCOUNT_GITHUB)


def _needs_signin():
    raise KeyError("You are not signed in. Open the console and press Sign in.")


def _unwrap(result):
    """Turn a Problem into the error the interface shows, or pass the data on."""
    if isinstance(result, github.Problem):
        if result.needs_signin:
            CONSOLE["who"] = None
        raise KeyError(result.message)
    return result


def r_console_status(handler, data):
    """Enough for the console to decide what to draw. Makes no network calls."""
    return {
        "configured": bool(_client_id()),
        "client_hint": _client_id()[:8] if _client_id() else "",
        "signed_in": bool(_token()),
        "who": CONSOLE["who"],
        "keychain": keychain.available(),
        "root": CONSOLE["root"],
        "root_name": os.path.basename(CONSOLE["root"]) if CONSOLE["root"] else None,
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
    who = github.whoami(token)
    if isinstance(who, github.Problem):
        CONSOLE["who"] = None
        raise KeyError(who.message)
    CONSOLE["who"] = who["name"]
    return {"signed_in": True, "who": who["name"]}


def r_console_signout(handler, data):
    keychain.delete(keychain.ACCOUNT_GITHUB)
    CONSOLE["who"] = None
    CONSOLE["signin"] = None
    return {"signed_in": False}


def r_console_pick_vault(handler, data):
    start = CONSOLE["root"] or os.path.expanduser("~")
    path, err = picker.choose_folder(
        "Choose your vault folder (the folder holding your chapters)", start
    )
    if err:
        return {"error": err}
    if not path:
        return {"cancelled": True}
    CONSOLE["root"] = path.rstrip("/")
    return {"root": CONSOLE["root"],
            "root_name": os.path.basename(CONSOLE["root"])}


def r_console_load(handler, data):
    """Everything waiting, in one call.

    Each part fails on its own: one thing being unavailable never blanks the
    rest of the screen.
    """
    token = _token()
    if not token:
        _needs_signin()

    out = {"suggestions": [], "drafts": [], "weekly": [], "publish": None,
           "problems": [], "offline": False}

    if CONSOLE["who"] is None:
        who = github.whoami(token)
        if isinstance(who, github.Problem):
            if who.needs_signin:
                raise KeyError(who.message)
            out["problems"].append(who.message)
            out["offline"] = who.offline
        else:
            CONSOLE["who"] = who["name"]
    out["who"] = CONSOLE["who"]

    issues = github.suggested_edits(token)
    if isinstance(issues, github.Problem):
        out["problems"].append("Suggestions: " + issues.message)
        out["offline"] = out["offline"] or issues.offline
    else:
        out["suggestions"] = [console.parse_suggestion(i) for i in issues]

    prs = github.draft_changes(token)
    if isinstance(prs, github.Problem):
        out["problems"].append("Draft changes: " + prs.message)
        out["offline"] = out["offline"] or prs.offline
    else:
        out["drafts"] = [console.describe_change(p) for p in prs]

    publish, publish_problem = _publish_state(token)
    if publish_problem is not None:
        out["problems"].append("Going live: " + publish_problem.message)
        out["offline"] = out["offline"] or publish_problem.offline
    else:
        out["publish"] = publish

    weekly = github.weekly_status(token)
    if isinstance(weekly, github.Problem):
        out["problems"].append("Weekly jobs: " + weekly.message)
        out["offline"] = out["offline"] or weekly.offline
    else:
        out["weekly"] = weekly

    return out


def r_console_plan(handler, data):
    """Work out, without changing anything, what accepting would do."""
    number = data.get("number")
    path = data.get("path") or ""
    text = data.get("suggestion") or ""
    plan = console.plan_change(CONSOLE["root"], path, text)
    CONSOLE["plans"][str(number)] = plan
    return {
        "can_apply": plan["can_apply"],
        "reason": plan["reason"],
        "line_no": plan["line_no"],
        "before": plan["before"],
        "after": plan["after"],
        "needs_vault": not CONSOLE["root"],
    }


def r_console_accept(handler, data):
    """Accept a suggestion: optionally make the change, then reply and close.

    The chapter is written first. If the reply or the closing then fails, the
    author is told plainly that the text is fixed but the suggestion is still
    open — which is recoverable — rather than the other way round.
    """
    token = _token()
    if not token:
        _needs_signin()

    number = data.get("number")
    apply_it = bool(data.get("apply"))
    steps = []

    if apply_it:
        plan = CONSOLE["plans"].get(str(number))
        if not plan:
            raise KeyError("Please look at the change again before accepting it.")
        ok, message = console.apply_change(plan)
        if not ok:
            raise KeyError(message)
        steps.append(message)

    replied = github.comment(token, number, console.THANKS)
    if isinstance(replied, github.Problem):
        raise RuntimeError(
            (("The chapter was updated. " if apply_it else "") +
             "The thank-you could not be sent: " + replied.message)
        )
    steps.append("A thank-you was sent.")

    github.drop_label(token, number, console.NEEDS_TRIAGE)

    closed = github.close_issue(token, number)
    if isinstance(closed, github.Problem):
        raise RuntimeError(
            (("The chapter was updated and the thank-you was sent, but the "
              "suggestion could not be marked as dealt with: ") + closed.message)
        )
    steps.append("The suggestion was marked as dealt with.")
    CONSOLE["plans"].pop(str(number), None)
    return {"done": True, "steps": steps}


def r_console_decline(handler, data):
    token = _token()
    if not token:
        _needs_signin()
    number = data.get("number")

    replied = github.comment(token, number, console.DECLINED)
    if isinstance(replied, github.Problem):
        raise RuntimeError("The reply could not be sent: " + replied.message)

    github.drop_label(token, number, console.NEEDS_TRIAGE)

    closed = github.close_issue(token, number)
    if isinstance(closed, github.Problem):
        raise RuntimeError(
            "The reply was sent, but the suggestion could not be marked as "
            "dealt with: " + closed.message
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
# area is shared: it holds whatever has been written in the browser editor as
# well as whatever has been accepted here, so merging it would publish other
# people's unreviewed work on the strength of one press about one change. The
# live book is also protected and has checks of its own, and the author's vault
# tracks it, so writing to it behind the author's back would leave that copy
# silently out of date. Publishing is therefore its own deliberate press, on its own
# screen, showing what would go — the same reasoning that keeps the weekly
# generated pull requests out of this app.


def _publish_state(token, tries=1):
    """What stands between the drafts area and the live book.

    Reads; never opens or changes anything. Returns (info, problem). `info` is
    None when the live book already has everything the drafts area holds.
    """
    compare = github.drafts_ahead_of_live(token)
    if isinstance(compare, github.Problem):
        return None, compare
    existing = github.open_publish_request(token)
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

    state = github.mergeability(token, existing.get("number"), tries=tries)
    info = console.describe_publish(existing, compare, state)
    info["open"] = True
    info["waiting"] = False
    return info, None


def _open_or_refresh_publish_request(token):
    """Make sure the one publish request is open and says what the drafts area
    now holds. Returns (info, problem).

    There is only ever one, and its description is rewritten rather than added
    to, because what it carries is the drafts area as it stands — including
    anything written in the browser editor — and not the change just accepted.
    """
    compare = github.drafts_ahead_of_live(token)
    if isinstance(compare, github.Problem):
        return None, compare
    existing = github.open_publish_request(token)
    if isinstance(existing, github.Problem):
        return None, existing

    if existing is None and int(compare.get("ahead_by") or 0) <= 0:
        return None, None

    body = console.publish_request_body(compare)
    if existing is not None:
        pr = github.update_publish_request(
            token, existing.get("number"), console.PUBLISH_TITLE, body)
        if isinstance(pr, github.Problem):
            # The request is open and carries the change whatever happens to
            # its description; only the description is now out of date, which
            # is not worth telling the author the accept failed over.
            pr = existing
        opened = False
    else:
        pr = github.create_publish_request(token, console.PUBLISH_TITLE, body)
        opened = True
        if isinstance(pr, github.Problem):
            # Another copy of the app, or the author on the web, may have opened
            # one in the moment between looking and asking.
            again = github.open_publish_request(token)
            if isinstance(again, github.Problem) or again is None:
                return None, pr
            pr, opened = again, False

    # Just opened or just changed, so the answer is not ready yet; wait a little
    # rather than report "not known" when a moment would settle it.
    state = github.mergeability(token, pr.get("number"), tries=4)
    info = console.describe_publish(pr, compare, state)
    info["open"] = True
    info["waiting"] = False
    info["opened"] = opened
    return info, None


def r_console_draft_detail(handler, data):
    token = _token()
    if not token:
        _needs_signin()
    files = github.change_files(token, data.get("number"))
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
    number = data.get("number")
    title = data.get("title") or "Accepted draft change"

    result = github.accept_change(token, number, title)
    if isinstance(result, github.Problem):
        raise RuntimeError(result.message)

    steps = ["The change was folded into the drafts area."]

    info, problem = _open_or_refresh_publish_request(token)
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
    info, problem = _open_or_refresh_publish_request(token)
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
    existing = github.open_publish_request(token)
    if isinstance(existing, github.Problem):
        raise RuntimeError(existing.message)
    if existing is None or int(existing.get("number") or 0) != number:
        raise KeyError(
            "What is waiting to go live has changed since this screen was "
            "drawn, so nothing was published. Press “Check again” and look at "
            "it before publishing."
        )

    result = github.publish(token, number, console.PUBLISH_TITLE)
    if isinstance(result, github.Problem):
        raise RuntimeError(result.message)
    if not result.get("merged"):
        raise RuntimeError(
            "The service did not confirm that this was published, so it may "
            "not have been. Press “Check again” and look before trying once "
            "more."
        )
    return {"done": True, "steps": list(console.PUBLISHED_STEPS)}


def r_console_draft_decline(handler, data):
    token = _token()
    if not token:
        _needs_signin()
    result = github.close_change(token, data.get("number"))
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
    "/api/save-key": r_savekey,
    "/api/quit": r_quit,

    "/api/import/status": r_import_status,
    "/api/import/install": r_import_install,
    "/api/import/pick-docx": r_import_pick_docx,
    "/api/import/pick-folder": r_import_pick_folder,
    "/api/import/convert": r_import_convert,
    "/api/import/save": r_import_save,
    "/api/import/cancel": r_import_cancel,

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
