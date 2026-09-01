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
from .edits import Edit, apply_edits
from .mdmap import DocMap

NEEDS_TRIAGE = "needs-triage"

THANKS = (
    "Thank you for this — it has been taken on board and the chapter has been "
    "updated.\n\n_Replied from the author's console._"
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

    count = text.count(pair["old"])
    if count == 0:
        out["reason"] = (
            f"The wording “{pair['old']}” is not in that chapter any "
            "more — it may already have been fixed."
        )
        return out
    if count > 1:
        out["reason"] = (
            f"The wording “{pair['old']}” appears {_times(count)} in that "
            "chapter, so the tool cannot tell which one is meant."
        )
        return out

    docmap = DocMap(text, full)
    for i, line in enumerate(docmap.lines):
        start = line.find(pair["old"])
        if start == -1:
            continue
        out.update({
            "can_apply": True,
            "file_path": full,
            "old": pair["old"],
            "new": pair["new"],
            "line_no": i + 1,
            "before": line,
            "after": line[:start] + pair["new"] + line[start + len(pair["old"]):],
            "reason": "",
        })
        return out

    out["reason"] = "That wording spans more than one line, so it was left alone."
    return out


def apply_change(plan):
    """Write the one line the plan describes. Returns (ok, message).

    Goes through the same machinery as the chapter analyses, so the same
    guarantee holds: only the line being changed is touched, and every other line
    is copied through byte for byte.
    """
    full = plan.get("file_path")
    if not plan.get("can_apply") or not full:
        return False, "There is nothing that can be applied automatically."

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
