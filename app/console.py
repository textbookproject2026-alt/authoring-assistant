"""
The author's console: what is waiting, and what to do about it.

github.py fetches; this module decides what any of it means and how to say it.
Nothing in the text produced here uses the vocabulary of the service underneath —
the author reads "suggestion", "draft change", "accept", "publish" and "history",
and never needs to learn anything else.

The one rule worth stating plainly, because it is the rule the whole design turns
on: a reader's suggestion is prose, not an instruction a machine can carry out.
"Accept" therefore does NOT mean "let the tool rewrite the chapter". It means the
author is taking it on. The tool only offers to make the change itself in the one
case where the suggestion contains an exact replacement, the old wording appears
in the chapter exactly once, and there is consequently nothing to guess. Anything
else goes to the author to do by hand, with the suggestion pinned beside it.
"""

import os
import re

from . import github
from .registry import within
from .edits import Edit, apply_edits
from .mdmap import DocMap

NEEDS_TRIAGE = "needs-triage"

THANKS = (
    "Thank you for this — it has been taken on board and the chapter has been "
    "updated.\n\n_Replied from the author's console._"
)


def thanks_with_change(url):
    """The thank-you when the tool made the change in the drafts area."""
    return (
        "Thank you for this — it has been taken on board, and the chapter has "
        f"been changed in the drafts: {url}\n\nIt reaches readers the next "
        "time the book goes live.\n\n_Replied from the author's console._"
    )
DECLINED = (
    "Thank you for taking the time to send this. After a look, the text is going "
    "to stay as it is for now — but the suggestion was read and appreciated, and "
    "please do send more.\n\n_Replied from the author's console._"
)


# --- reading a suggestion ----------------------------------------------------

def _fenced_block(body, heading):
    """The text inside the fenced block that follows a heading."""
    idx = body.find(heading)
    if idx == -1:
        return ""
    rest = body[idx + len(heading):]
    m = re.search(r"^(`{3,})[^\n]*\n(.*?)^\1\s*$", rest, re.S | re.M)
    if not m:
        return ""
    return m.group(2).rstrip("\n")


def parse_suggestion(issue):
    """Turn one raw suggestion into something worth showing."""
    body = issue.get("body") or ""
    title = issue.get("title") or ""

    path = ""
    m = re.match(r"\s*Suggested edit:\s*(.+?)\s*$", title)
    if m:
        path = m.group(1).strip()
    if not path:
        m = re.search(r"\*\*File:\*\*\s*\[`([^`]+)`\]", body)
        if m:
            path = m.group(1).strip()

    who = ""
    m = re.search(r"\*\*Submitted by:\*\*\s*`([^`]*)`", body)
    if m:
        who = m.group(1).strip()

    return {
        "number": issue.get("number"),
        "who": who or "someone who did not leave a name",
        "path": path,
        "page": _page_name(path),
        "suggestion": _fenced_block(body, "### Suggested edit"),
        "reasoning": _fenced_block(body, "### Reasoning"),
        "when": issue.get("created_at"),
        "url": issue.get("html_url", ""),
    }


def _page_name(path):
    """'chapters/chapter-03.md' -> 'chapter-03'."""
    if not path:
        return "an unknown page"
    return os.path.splitext(os.path.basename(path))[0]


def _times(n):
    """Counts the way a person writes them, not the way a computer does."""
    words = {2: "twice", 3: "three times", 4: "four times", 5: "five times"}
    return words.get(n, f"{n} times")


# --- deciding whether the tool may make the change itself --------------------

# Only these shapes count. Each must name the old wording and the new wording
# unambiguously, in quotes. Anything looser is a judgement call, and judgement
# calls belong to the author.
_PATTERNS = [
    r"""["“'](?P<old>[^"”']{2,200})["”']\s*(?:should\s+(?:be|read)|→|->|=>)\s*["“'](?P<new>[^"”']{0,200})["”']""",
    r"""change\s+["“'](?P<old>[^"”']{2,200})["”']\s+to\s+["“'](?P<new>[^"”']{0,200})["”']""",
    r"""replace\s+["“'](?P<old>[^"”']{2,200})["”']\s+with\s+["“'](?P<new>[^"”']{0,200})["”']""",
]


def literal_replacement(suggestion_text):
    """Find an unambiguous old/new pair, or None.

    Returns None whenever there is more than one candidate — two competing
    replacements in one suggestion is exactly the case where guessing is wrong.
    """
    found = []
    for pattern in _PATTERNS:
        for m in re.finditer(pattern, suggestion_text, re.I):
            old, new = m.group("old"), m.group("new")
            if old and old != new:
                found.append((old, new))
    # The same pair matched by two patterns is still one intention.
    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        return None
    return {"old": unique[0][0], "new": unique[0][1]}


def plan_change(vault_root, path, suggestion_text):
    """Work out whether this suggestion can be applied mechanically.

    Returns a dict describing what would happen. `can_apply` is only ever true
    when the old wording was found exactly once in the chapter.
    """
    out = {
        "can_apply": False,
        "reason": "",
        "vault": vault_root or "",
        "file_path": "",
        "old": "",
        "new": "",
        "line_no": None,
        "before": "",
        "after": "",
    }

    pair = literal_replacement(suggestion_text or "")
    if not pair:
        out["reason"] = (
            "This suggestion is written as a comment rather than as an exact "
            "replacement, so the tool will not change the chapter itself."
        )
        return out

    full = os.path.join(vault_root, path) if (vault_root and path) else ""
    # The page is named by whoever filed the suggestion. A name that leads out
    # of the vault ("../", a link to somewhere else) is never followed.
    if full and not within(vault_root, full):
        out["reason"] = (
            "This suggestion names a page outside the folder you opened, so "
            "the tool will not touch it."
        )
        return out
    if not full or not os.path.isfile(full):
        out["reason"] = (
            f"The page “{_page_name(path)}” is not in the folder you "
            "opened, so the tool cannot look at it."
        )
        return out

    try:
        with open(full, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        out["reason"] = "That chapter could not be read from disk."
        return out

    out.update(_find_once(text, pair, full))
    return out


def _find_once(text, pair, where):
    """Where in `text` the old wording is, if it is there exactly once and on
    one line. The fields plan_change fills in."""
    count = text.count(pair["old"])
    if count == 0:
        return {"reason": (
            f"The wording “{pair['old']}” is not in that chapter any "
            "more — it may already have been fixed.")}
    if count > 1:
        return {"reason": (
            f"The wording “{pair['old']}” appears {_times(count)} in that "
            "chapter, so the tool cannot tell which one is meant.")}

    docmap = DocMap(text, where)
    for i, line in enumerate(docmap.lines):
        start = line.find(pair["old"])
        if start == -1:
            continue
        return {
            "can_apply": True,
            "file_path": where,
            "old": pair["old"],
            "new": pair["new"],
            "line_no": i + 1,
            "before": line,
            "after": line[:start] + pair["new"] + line[start + len(pair["old"]):],
            "reason": "",
        }
    return {"reason": "That wording spans more than one line, so it was left alone."}


def plan_in_text(text, path, suggestion_text):
    """plan_change, for a chapter's text as the drafts area holds it.

    `text` is None when the page isn't in the drafts area. The plan carries
    the text it was worked out on, so that applying it can prove nothing
    else moved.
    """
    out = {"can_apply": False, "reason": "", "vault": "", "file_path": "",
           "old": "", "new": "", "line_no": None, "before": "", "after": "",
           "text": text}
    pair = literal_replacement(suggestion_text or "")
    if not pair:
        out["reason"] = (
            "This suggestion is written as a comment rather than as an exact "
            "replacement, so the tool will not change the chapter itself."
        )
        return out
    if text is None:
        out["reason"] = (
            f"The page “{_page_name(path)}” is not in the drafts area, so the "
            "tool cannot look at it."
        )
        return out
    out.update(_find_once(text, pair, path))
    return out


def change_text(plan):
    """The chapter's new text for a drafts plan. Returns (text, None), or
    (None, why). Only the one line changes; every other line is copied through
    byte for byte, and that is checked here rather than trusted."""
    text = plan.get("text")
    if not plan.get("can_apply") or text is None:
        return None, "There is nothing that can be applied automatically."
    docmap = DocMap(text, plan.get("file_path"))
    line_index = int(plan["line_no"]) - 1
    if not 0 <= line_index < len(docmap.lines) or \
            docmap.lines[line_index] != plan["before"]:
        return None, "That chapter has changed since this was worked out."
    start = plan["before"].find(plan["old"])
    edit = Edit(line_index, start, start + len(plan["old"]), plan["new"],
                origin="suggestion")
    new_text, changed = apply_edits(docmap, [edit])
    new_map = DocMap(new_text, plan.get("file_path"))
    if changed != {line_index} or len(new_map.lines) != len(docmap.lines) or \
            any(a != b for i, (a, b) in enumerate(zip(docmap.lines, new_map.lines))
                if i != line_index):
        return None, "The change did not come out as expected, so nothing was sent."
    return new_text, None


def apply_change(plan):
    """Write the one line the plan describes. Returns (ok, message).

    Goes through the same machinery as the chapter analyses, so the same
    guarantee holds: only the line being changed is touched, and every other line
    is copied through byte for byte.
    """
    full = plan.get("file_path")
    if not plan.get("can_apply") or not full:
        return False, "There is nothing that can be applied automatically."
    if not plan.get("vault") or not within(plan["vault"], full):
        return False, (
            "That page is no longer inside the folder the change was worked "
            "out for, so nothing was saved."
        )

    try:
        with open(full, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False, "That chapter could not be read from disk."

    docmap = DocMap(text, full)
    line_index = int(plan["line_no"]) - 1
    if line_index < 0 or line_index >= len(docmap.lines):
        return False, "That chapter has changed since this was worked out."

    line = docmap.lines[line_index]
    start = line.find(plan["old"])
    if start == -1 or line != plan["before"]:
        return False, (
            "That chapter changed on disk since this was worked out. Nothing was "
            "saved — take another look."
        )

    edit = Edit(line_index, start, start + len(plan["old"]), plan["new"],
                origin="suggestion")
    new_text, changed = apply_edits(docmap, [edit])
    if len(changed) != 1:
        return False, "The change did not come out as expected, so nothing was saved."

    tmp = full + ".aa-tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        os.replace(tmp, full)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "That chapter could not be written to."
    return True, f"Line {plan['line_no']} was updated."


# --- draft changes, described as prose ---------------------------------------

MAX_READABLE_LINES = 60
MAX_READABLE_PAGES = 5


def readable_change(files):
    """Turn a set of changed files into before/after prose.

    Above a certain size this refuses and says so, rather than producing a wall
    of text nobody will read. The console then offers the browser instead.
    """
    if isinstance(files, github.Problem):
        return {"readable": False, "why": files.message, "pages": []}

    total = sum(int(f.get("changes") or 0) for f in files)
    pages = []
    for f in files:
        pages.append({
            "page": _page_name(f.get("filename", "")),
            "path": f.get("filename", ""),
            "added": int(f.get("additions") or 0),
            "removed": int(f.get("deletions") or 0),
            "lines": _prose_lines(f.get("patch") or ""),
        })

    if total > MAX_READABLE_LINES or len(files) > MAX_READABLE_PAGES:
        return {
            "readable": False,
            "why": (
                f"This is a large change — {total} lines across "
                f"{len(files)} page{'s' if len(files) != 1 else ''}. It is easier "
                "to read in your browser."
            ),
            "pages": [{"page": p["page"], "path": p["path"], "added": p["added"],
                       "removed": p["removed"], "lines": []} for p in pages],
        }

    return {"readable": True, "why": "", "pages": pages}


def _prose_lines(patch):
    """The added and removed lines, without any of the surrounding notation."""
    out = []
    for raw in patch.split("\n"):
        if raw.startswith("@@") or raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("+"):
            out.append({"kind": "after", "text": raw[1:]})
        elif raw.startswith("-"):
            out.append({"kind": "before", "text": raw[1:]})
    return out


def describe_change(pr):
    """One proposed change, in the author's vocabulary."""
    user = pr.get("user") or {}
    return {
        "number": pr.get("number"),
        "who": user.get("login", "someone"),
        "title": pr.get("title", "Untitled change"),
        "when": pr.get("created_at"),
        "url": pr.get("html_url", ""),
    }


# --- getting the drafts to readers -------------------------------------------
#
# Accepting a change puts it in the drafts area and no further. The drafts area
# is shared — it also holds whatever has been published from the browser editor — so
# what goes to readers is always the drafts area as a whole, never one change on
# its own. That is what the publish request describes, and why its description
# is rewritten every time rather than added to.

PUBLISH_TITLE = "Publish the drafts to the live book"

PUBLISH_INTRO = (
    "Everything now waiting in the drafts area, gathered so that it can go to "
    "readers in one go.\n\n"
    "This is not only the change that was accepted most recently. The drafts "
    "area also holds anything published from the browser editor, so what follows is "
    "the drafts area exactly as it stood when this description was last "
    "rewritten. Merging this is what publishes it; until then nothing here has "
    "reached a reader."
)

PUBLISH_FOOTER = "_Opened and kept up to date by the author's console._"

PUBLISH_NOT_OPEN = (
    "There is work in the drafts area that has not been put in line for the "
    "live book yet. Press “Put it in line” and it will be."
)

PUBLISHED_STEPS = [
    "The drafts were sent to the live book.",
    "The site rebuilds itself from there, which takes a few minutes. Readers "
    "see the change once it has.",
    "Your vault does not know about this yet. Take the latest into Obsidian "
    "before you write there again, or your copy and the live book will "
    "disagree with each other.",
]

# For a book whose site is built from the repository, not published from the
# author's folder. The drafts area is where the author carries on, so there is
# no copy to bring up to date.
PUBLISHED_STEPS_BUILT = [
    "The drafts were sent to the live book.",
    "The site rebuilds itself from there, which takes a few minutes. Readers "
    "see the change once it has.",
    "The drafts area and the live book now hold the same text, so you can "
    "carry on in the drafts area straight away.",
]


def published_steps(book):
    """What the author is told once the drafts have gone live.

    The Obsidian wording stays for a book still published from the author's
    folder (its registry entry says "obsidian-publish") and goes once that
    changes.
    """
    return list(PUBLISHED_STEPS if book.from_folder else PUBLISHED_STEPS_BUILT)

# How many of each to name before saying "and more". A description nobody can
# read is no more honest than no description at all.
MAX_LISTED = 40


def publish_request_body(compare):
    """What the publish request says about itself.

    Written from the comparison rather than from the change just accepted, so it
    describes the drafts area as it stands — the whole of it, whoever wrote it.
    """
    compare = compare if isinstance(compare, dict) else {}
    files = [f for f in (compare.get("files") or []) if isinstance(f, dict)]
    commits = [c for c in (compare.get("commits") or []) if isinstance(c, dict)]

    out = [PUBLISH_INTRO]

    if files:
        out.append("")
        out.append(f"**Pages this would change ({len(files)})**")
        out.append("")
        for f in files[:MAX_LISTED]:
            out.append(
                f"- `{f.get('filename', '')}` — "
                f"{int(f.get('additions') or 0)} added, "
                f"{int(f.get('deletions') or 0)} removed"
            )
        if len(files) > MAX_LISTED:
            out.append(f"- …and {len(files) - MAX_LISTED} more")

    if commits:
        out.append("")
        out.append(f"**What is in the drafts area ({len(commits)})**")
        out.append("")
        for c in commits[-MAX_LISTED:]:
            out.append(f"- {_commit_line(c)}")
        if len(commits) > MAX_LISTED:
            out.append(f"- …and {len(commits) - MAX_LISTED} older")

    out.append("")
    out.append(PUBLISH_FOOTER)
    return "\n".join(out)


def _commit_line(commit):
    """One line of the drafts area: what was done, and by whom."""
    body = (commit.get("commit") or {}).get("message") or ""
    subject = body.split("\n")[0].strip() or "an untitled change"
    return f"{subject} — {_commit_author(commit)}"


def _commit_author(commit):
    author = commit.get("author") or {}
    if author.get("login"):
        return author["login"]
    named = ((commit.get("commit") or {}).get("author") or {}).get("name")
    return named or "someone"


# What each state means for the author, said without the vocabulary underneath.
# "unknown" is a real answer and is given as one: the tool does not claim a
# change can be published when it has not been told so.
PUBLISH_STATE_WORDS = {
    "clean": (
        "This can go to readers now. Nothing else is waiting on it."
    ),
    "conflict": (
        "This cannot be published as it stands: the same wording has been "
        "changed both in the drafts area and in the live book, and the tool "
        "will not choose between them. Nothing was lost and nothing has been "
        "undone — open it in your browser to settle which wording wins, or "
        "publish your own copy from Obsidian first and check back here."
    ),
    # The same, for a book built from the repository: there is no copy of
    # the author's own to publish first.
    "conflict_built": (
        "This cannot be published as it stands: the same wording has been "
        "changed both in the drafts area and in the live book, and the tool "
        "will not choose between them. Nothing was lost and nothing has been "
        "undone — open it in your browser to settle which wording wins, then "
        "check back here."
    ),
    "blocked": (
        "This is waiting on the book's own checks before it can go to readers. "
        "That usually takes a few minutes; press “Check again” shortly."
    ),
    "unknown": (
        "Whether this can go to readers is still being worked out. It is safely "
        "in the drafts area either way — press “Check again” in a moment."
    ),
}


def publish_state_words(state, book):
    if state == "conflict" and not book.from_folder:
        state = "conflict_built"
    return PUBLISH_STATE_WORDS.get(state, PUBLISH_STATE_WORDS["unknown"])


def describe_publish(pr, compare, state, book):
    """The publish request, in the author's vocabulary."""
    compare = compare if isinstance(compare, dict) else {}
    files = [f for f in (compare.get("files") or []) if isinstance(f, dict)]
    commits = [c for c in (compare.get("commits") or []) if isinstance(c, dict)]

    who = []
    for c in commits:
        name = _commit_author(c)
        if name not in who:
            who.append(name)

    return {
        "number": pr.get("number"),
        "url": pr.get("html_url", ""),
        "when": pr.get("created_at"),
        "pages": [_page_name(f.get("filename", "")) for f in files],
        "page_count": len(files),
        "change_count": len(commits),
        "who": who,
        "state": state,
        "state_words": publish_state_words(state, book),
        "can_publish": state == "clean",
    }
