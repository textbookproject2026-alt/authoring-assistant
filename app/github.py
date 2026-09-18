"""
Talking to the service that stores the textbook.

This module is plumbing only. It knows about tokens, endpoints and status codes;
it does not know how any of that should be described to the author. The words
the author reads are written in console.py.

Signing in uses the device flow: the app shows a short code, the author types it
into a page on the web, and the app collects the token afterwards. That flow was
chosen because it needs no secret shipped inside the application, no callback
address, and no relay of our own to keep running.

Nothing here ever raises for an ordinary failure. Every call returns either a
result or a Problem, and a Problem carries a plain-English sentence plus a flag
saying whether signing in again would fix it.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

# Which repository, and which branches, belong to the book being worked on is
# never written here. Every call that touches a book takes the `Book` the
# registry resolved (registry.py) and uses only its values.
#
# Each book has two branches the console cares about. Everything proposed — a
# contributor's change accepted here, or a draft written in the browser CMS —
# lands on the drafts branch. The live branch is what readers see. Nothing
# reaches the live branch except through a pull request from the drafts branch,
# which is the only door and is the door the console opens.

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"

# Every registered repository is public, so this is the narrowest scope that still allows
# closing a suggestion, leaving a reply, and accepting a draft change. It
# deliberately gives no access to any private work the author may have.
SCOPE = "public_repo"

USER_AGENT = "Authoring-Assistant"
TIMEOUT = 30

# The four jobs that run themselves every week, and the file each one lives in.
WEEKLY_JOBS = [
    ("backup-annotations.yml", "Saving a copy of reader comments"),
    ("contributors.yml", "Updating the contributors page"),
    ("derivatives.yml", "Updating the department editions page"),
    ("dashboard.yml", "Rebuilding the project health page"),
]


class Problem:
    """Something did not work, described the way the author should hear it."""

    def __init__(self, message, needs_signin=False, offline=False, code=None):
        self.message = message
        self.needs_signin = needs_signin
        self.offline = offline
        self.code = code

    def __repr__(self):
        return f"Problem({self.message!r})"


def _request(method, url, token=None, payload=None, accept="application/vnd.github+json"):
    """One HTTP call. Returns parsed JSON, or a Problem."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            if not body.strip():
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as e:
        return _http_problem(e)
    except urllib.error.URLError:
        return Problem(
            "This Mac is not online, so nothing could be fetched.", offline=True
        )
    except (TimeoutError, OSError):
        return Problem(
            "The connection timed out. Try again in a moment.", offline=True
        )
    except json.JSONDecodeError:
        return Problem("An unreadable reply came back. Nothing was changed.")


def _http_problem(e):
    problem = _describe_http(e)
    problem.code = e.code
    return problem


def _describe_http(e):
    code = e.code
    try:
        detail = json.loads(e.read().decode("utf-8", "replace"))
        note = str(detail.get("message", ""))
    except Exception:
        note = ""

    if code in (401,):
        return Problem(
            "Your sign-in is no longer accepted. Please sign in again.",
            needs_signin=True,
        )
    if code == 403 and "rate limit" in note.lower():
        return Problem(
            "The service is asking us to slow down. Wait a few minutes and try "
            "again."
        )
    if code == 403:
        return Problem(
            "Your sign-in does not have permission to do that. Signing in again "
            "may fix it; if not, ask the technical contact.",
            needs_signin=True,
        )
    if code == 404:
        return Problem("That item no longer exists — it may have been dealt with already.")
    if code == 409:
        return Problem(
            "That change could not be applied cleanly. Open it in your browser "
            "to look at it."
        )
    if code == 405:
        return Problem(
            "That could not be merged as it stands, and nothing was changed. "
            "Open it in your browser to see what is holding it up."
        )
    if code == 422:
        return Problem(
            "The service refused that as invalid. Nothing was changed. "
            + (note or "")
        )
    if 500 <= code < 600:
        return Problem("The service is having trouble. Nothing was changed — try later.")
    return Problem(f"The service replied with an error ({code}). Nothing was changed.")


# --- signing in --------------------------------------------------------------

def start_signin(client_id):
    """Ask for a code the author types into a web page."""
    if not client_id:
        return Problem(
            "This copy has not been set up for signing in yet. Ask the "
            "technical contact to add the sign-in identifier in Settings."
        )
    result = _form_post(DEVICE_CODE_URL, {"client_id": client_id, "scope": SCOPE})
    if isinstance(result, Problem):
        return result
    if "device_code" not in result:
        return Problem(
            "Signing in could not be started. Check the sign-in identifier in "
            "Settings, and that Device Flow is switched on for it."
        )
    return {
        "device_code": result["device_code"],
        "user_code": result.get("user_code", ""),
        "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
        "interval": int(result.get("interval", 5)),
        "expires_in": int(result.get("expires_in", 900)),
    }


def _form_post(url, fields):
    """The sign-in endpoints take a form and, asked nicely, reply with JSON."""
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _http_problem(e)
    except urllib.error.URLError:
        return Problem("This Mac is not online, so signing in cannot start.", offline=True)
    except (TimeoutError, OSError):
        return Problem("The connection timed out while signing in.", offline=True)
    except json.JSONDecodeError:
        return Problem("An unreadable reply came back while signing in.")


def poll_signin(client_id, device_code):
    """Check once whether the author has finished approving.

    Returns {"token": ...}, {"waiting": True}, {"slow_down": n}, or a Problem.
    """
    result = _form_post(TOKEN_URL, {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })
    if isinstance(result, Problem):
        return result

    token = result.get("access_token")
    if token:
        return {"token": token}

    error = result.get("error", "")
    if error == "authorization_pending":
        return {"waiting": True}
    if error == "slow_down":
        return {"slow_down": int(result.get("interval", 10))}
    if error == "expired_token":
        return Problem("That code ran out before it was used. Start signing in again.")
    if error == "access_denied":
        return Problem("Sign-in was refused on the web page. Nothing was changed.")
    if error == "incorrect_client_credentials":
        return Problem(
            "The sign-in identifier in Settings is not recognised. Ask the "
            "technical contact to check it."
        )
    return Problem("Signing in did not complete. Please try again.")


def whoami(token):
    """Who the saved token belongs to, which is also how we test it."""
    result = _request("GET", f"{API}/user", token=token)
    if isinstance(result, Problem):
        return result
    return {
        "login": result.get("login", ""),
        "name": result.get("name") or result.get("login", ""),
    }


def repo_access(token, book):
    """Whether the signed-in author can act on this book.

    Returns "write", "read" or "none", or a Problem. Acting — closing a
    suggestion, accepting a change, publishing — needs write access, so only
    books answering "write" are offered.
    """
    result = _request("GET", f"{API}/repos/{book.repo}", token=token)
    if isinstance(result, Problem):
        if result.code == 404 or (result.code == 403
                                  and "slow down" not in result.message):
            return "none"   # a missing or hidden repository is not ours to use
        return result
    perms = result.get("permissions") or {}
    if not book.same_repo(result.get("full_name")) or result.get("private"):
        # Moved, or private (which the narrow sign-in scope cannot reach).
        return "none"
    if perms.get("admin") or perms.get("maintain") or perms.get("push"):
        return "write"
    return "read" if perms.get("pull") else "none"


# --- reading -----------------------------------------------------------------

def suggested_edits(token, book):
    """Open reader suggestions, newest first."""
    url = (f"{API}/repos/{book.repo}/issues"
           f"?labels=suggested-edit&state=open&sort=created&direction=desc&per_page=50")
    result = _request("GET", url, token=token)
    if isinstance(result, Problem):
        return result
    # The issues endpoint also returns proposed changes; those are handled
    # separately and must not appear twice.
    return [i for i in result if isinstance(i, dict) and "pull_request" not in i]


def from_book(item, book):
    """True if an issue or pull request really came from this book's repository.

    Checked on every item before it is shown, because a suggestion is only ever
    applied to the vault of the book it was filed against.
    """
    url = (item.get("repository_url") or "").rstrip("/")
    if url:
        return url.casefold() == f"{API}/repos/{book.repo}".casefold()
    base = ((item.get("base") or {}).get("repo") or {}).get("full_name")
    return book.same_repo(base)


def draft_changes(token, book):
    """Proposed changes waiting to go into the drafts area."""
    url = (f"{API}/repos/{book.repo}/pulls"
           f"?state=open&sort=created&direction=desc&per_page=50"
           f"&base={urllib.parse.quote(book.drafts_branch)}")
    result = _request("GET", url, token=token)
    if isinstance(result, Problem):
        return result
    return [p for p in result if isinstance(p, dict)]


def change_files(token, book, number):
    """Which files a proposed change touches, and how."""
    url = f"{API}/repos/{book.repo}/pulls/{number}/files?per_page=100"
    return _request("GET", url, token=token)


def file_at_ref(token, book, path, ref):
    """The text of one file as it stands on a given version."""
    url = (f"{API}/repos/{book.repo}/contents/"
           f"{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}")
    result = _request("GET", url, token=token,
                      accept="application/vnd.github.raw")
    return result


def weekly_status(token, book):
    """Whether each of the four weekly jobs last finished successfully."""
    out = []
    for filename, friendly in WEEKLY_JOBS:
        url = (f"{API}/repos/{book.repo}/actions/workflows/"
               f"{filename}/runs?per_page=1&status=completed")
        result = _request("GET", url, token=token)
        if isinstance(result, Problem):
            return result
        runs = result.get("workflow_runs") or []
        if not runs:
            out.append({"name": friendly, "state": "none", "when": None})
            continue
        run = runs[0]
        out.append({
            "name": friendly,
            "state": "ok" if run.get("conclusion") == "success" else "failed",
            "when": run.get("updated_at"),
            "url": run.get("html_url", ""),
        })
    return out


# --- writing -----------------------------------------------------------------

def comment(token, book, number, text):
    return _request(
        "POST",
        f"{API}/repos/{book.repo}/issues/{number}/comments",
        token=token,
        payload={"body": text},
    )


def close_issue(token, book, number):
    return _request(
        "PATCH",
        f"{API}/repos/{book.repo}/issues/{number}",
        token=token,
        payload={"state": "closed"},
    )


def drop_label(token, book, number, label):
    """Remove a label; a missing label is not an error worth surfacing."""
    url = (f"{API}/repos/{book.repo}/issues/{number}/labels/"
           f"{urllib.parse.quote(label)}")
    result = _request("DELETE", url, token=token)
    if isinstance(result, Problem):
        return None
    return result


def accept_change(token, book, number, title):
    """Fold a proposed change into the drafts area.

    Squashed, so one accepted change is one commit on the drafts branch however many
    times its author saved while writing it. This puts the change in the drafts
    area and nowhere else; getting it from there to readers is the job of the
    publish request below.
    """
    return _request(
        "PUT",
        f"{API}/repos/{book.repo}/pulls/{number}/merge",
        token=token,
        payload={"merge_method": "squash", "commit_title": title},
    )


def close_change(token, book, number):
    return _request(
        "PATCH",
        f"{API}/repos/{book.repo}/pulls/{number}",
        token=token,
        payload={"state": "closed"},
    )



# --- the one door from the drafts area to the live book ----------------------
#
# The drafts branch is long-lived and shared: an accepted change lands there, and
# so does every draft written in the browser CMS. So there is exactly one pull
# request from the drafts branch into the live branch at a time, and it carries whatever `drafts` holds at
# the moment it is looked at — not one change in isolation. Accepting reopens or
# refreshes that one request rather than opening a second.

def open_publish_request(token, book):
    """The pull request carrying the drafts to the live book, if one is open.

    Returns the pull request, None if there is none open, or a Problem.
    """
    url = (f"{API}/repos/{book.repo}/pulls?state=open"
           f"&base={urllib.parse.quote(book.live_branch)}"
           f"&head={urllib.parse.quote(book.owner + ':' + book.drafts_branch)}"
           f"&per_page=10")
    result = _request("GET", url, token=token)
    if isinstance(result, Problem):
        return result
    for pr in result:
        if isinstance(pr, dict):
            return pr
    return None


def drafts_ahead_of_live(token, book):
    """What the drafts area holds that the live book does not.

    The reply carries `ahead_by`, the commits and the files, which is everything
    needed to describe the publish request truthfully.
    """
    url = (f"{API}/repos/{book.repo}/compare/"
           f"{urllib.parse.quote(book.live_branch)}..."
           f"{urllib.parse.quote(book.drafts_branch)}")
    return _request("GET", url, token=token)


def create_publish_request(token, book, title, body):
    return _request(
        "POST",
        f"{API}/repos/{book.repo}/pulls",
        token=token,
        payload={"title": title, "body": body,
                 "head": book.drafts_branch, "base": book.live_branch},
    )


def update_publish_request(token, book, number, title, body):
    return _request(
        "PATCH",
        f"{API}/repos/{book.repo}/pulls/{number}",
        token=token,
        payload={"title": title, "body": body},
    )


# What the service calls the state of a pull request, and whether that means it
# can be merged. Anything not listed is treated as "not known", never as "fine".
_BLOCKED_STATES = ("blocked", "draft")


def mergeability(token, book, number, tries=1, pause=1.5):
    """Whether a pull request can be merged as it stands.

    The service works this out in the background, so for a moment after a pull
    request is opened or changed the only honest answer is that it is not known
    yet; `tries` says how long to wait for a real one.

    Returns "clean", "conflict", "blocked" or "unknown". It never guesses: a
    request whose state has not been worked out comes back as "unknown", and the
    author is told that rather than told it is fine.
    """
    for attempt in range(max(1, tries)):
        pr = _request("GET", f"{API}/repos/{book.repo}/pulls/{number}",
                      token=token)
        if isinstance(pr, Problem):
            return "unknown"
        mergeable = pr.get("mergeable")
        if mergeable is False:
            return "conflict"
        if mergeable is True:
            state = pr.get("mergeable_state") or ""
            return "blocked" if state in _BLOCKED_STATES else "clean"
        if attempt + 1 < tries:
            time.sleep(pause)
    return "unknown"


def publish(token, book, number, title):
    """Merge the drafts into the live book.

    A merge commit, not a squash. The drafts branch is long-lived, so it has to
    stay an ancestor of `main` (the live branch): squashing would rewrite the
    same work under a new commit and leave the drafts branch looking as though everything it had ever carried were
    still unpublished, offering all of it again on the next accept.
    """
    return _request(
        "PUT",
        f"{API}/repos/{book.repo}/pulls/{number}/merge",
        token=token,
        payload={"merge_method": "merge", "commit_title": title},
    )
