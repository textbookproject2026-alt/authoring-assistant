"""
Whether the drafts preview shows the drafts area as it stands.

A book on the platform's builder has a preview of its drafts branch, rebuilt
whenever the branch moves (BOOK-ONE-TO-QUARTZ §0a). Each build serves a public
marker, `/.well-known/textbook.json`, naming the book commit it was built from.
The console compares that commit with the drafts branch's head:

  current   the preview was built from the head: show the link;
  building  it wasn't, and the head is less than ten minutes old;
  stale     it wasn't, ten minutes on. The build failed or never started, and
            the author is the one person who would notice, so they are told;
  unknown   the head or the marker could not be read.

Nothing is remembered between checks. The head's commit time says how long the
preview has had, so the answer is the same after the app is restarted.
"""

import calendar
import json
import time
import urllib.error
import urllib.request

from . import github

STALE_AFTER = 10 * 60      # seconds, §0a
FETCH_TIMEOUT = 8
MARKER_LIMIT = 16 * 1024   # a marker is a few hundred bytes

CURRENT, BUILDING, STALE, UNKNOWN = "current", "building", "stale", "unknown"


def fetch_marker(url):
    """The served marker as a dict, None if there is none yet, or a Problem."""
    req = urllib.request.Request(
        f"{url}?t={int(time.time())}",
        headers={"User-Agent": github.USER_AGENT, "Accept": "application/json",
                 "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read(MARKER_LIMIT).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return github.Problem("The preview could not be reached.")
    except (urllib.error.URLError, OSError):
        return github.Problem("This Mac is not online.", offline=True)
    except ValueError:
        return github.Problem("The preview's marker could not be read.")
    return data if isinstance(data, dict) else github.Problem(
        "The preview's marker could not be read.")



def _stamp(iso):
    """Seconds since the epoch from GitHub's `2026-09-24T12:00:00Z`, or None."""
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def built_from(marker, book):
    """The book commit a marker says it was built from, if it is this book's
    drafts preview at all."""
    if not isinstance(marker, dict):
        return None
    if marker.get("slug") != book.slug or marker.get("branch") != book.drafts_branch:
        return None
    commit = marker.get("book_commit")
    return commit if isinstance(commit, str) else None


def check(token, book, now=None):
    """The drafts preview's state, or None for a book without one."""
    url = book.drafts_preview
    if not url:
        return None
    now = time.time() if now is None else now
    out = {"url": url, "state": UNKNOWN, "head": None, "built": None,
           "has_build": None, "waited": None, "offline": False}

    head = github.drafts_head_dated(token, book)
    marker = fetch_marker(book.drafts_marker_url)
    for got in (head, marker):
        if isinstance(got, github.Problem):
            out["offline"] = out["offline"] or got.offline
    if isinstance(head, github.Problem) or isinstance(marker, github.Problem):
        return out

    sha, when = head
    built = built_from(marker, book)
    out.update(head=sha, built=built, has_build=built is not None)
    if built == sha:
        out["state"] = CURRENT
        return out
    since = _stamp(when)
    out["waited"] = None if since is None else max(0, int(now - since))
    # A head with no readable time is given the benefit of the doubt: saying
    # "still building" wrongly costs less than a false alarm.
    stale = since is not None and now - since >= STALE_AFTER
    out["state"] = STALE if stale else BUILDING
    return out
