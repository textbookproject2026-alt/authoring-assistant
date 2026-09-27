"""
Analysis 4 - the AI formatting check.

Optional, like DeepSeek's glossary pass (llm.py): with no key it does nothing and
says why, and every other check works as before. The deterministic conversion
(convert.py) stays the default. This only ever proposes changes, and the author
approves each one in the same review as the other three analyses.

The knowledge base is formatting_rules.md, beside this file. The prompt is built
from it at run time, so a rule changes with no code change. What stays in code is
the reply format, and the promises the app makes whatever the rules say:

  * One line at a time. A proposal replaces one line with one line, so the
    chapter's line count never changes (session.build_preview asserts it). Fixes
    that would add or remove lines can only come back as notes.
  * Formatting only, never wording. A proposal is kept only if the line's text,
    with markup stripped and whitespace normalised, is the same before and after.
    Its web addresses, footnote labels and reference markers must be unchanged,
    and it may not add or remove a concept link, or point one at a page that
    doesn't exist. Anything else is thrown away, and the author is told.
  * Never in frontmatter or code, where "the text" can't be checked like prose.
"""

import os
import re

from . import llm

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "formatting_rules.md")

MAX_CHARS = 90000
MAX_PROPOSALS = 300
MAX_NOTES_SHOWN = 10

RULE_RE = re.compile(r"^- `([A-Z]+-\d+)`\s+(.*)$")
VERSION_RE = re.compile(r"^Version:\s*(.+?)\s*$", re.M)

CONTRACT = (
    "You check the markdown formatting of one chapter of an academic textbook "
    "against the platform's formatting rules, which follow. You fix markup only. "
    "You never change, add, remove or reorder a single word, number or punctuation "
    "mark of the author's text, and you never change a web address, a footnote "
    "label or a ^ref- marker. You never add or remove a [[concept link]].\n\n"
    "The chapter is given with each line prefixed by its line number and a tab. "
    "Each change replaces exactly one whole line with exactly one new line: never "
    "put a line break inside \"after\", and never propose adding or removing lines. "
    "Anything that would need lines added or removed goes in \"notes\" instead. "
    "Leave frontmatter (between the first two --- lines) and code blocks alone.\n\n"
    "Reply with JSON only, in the form "
    '{"changes": [{"line": 12, "rule": "HEAD-2", "before": "the whole line exactly '
    'as given", "after": "the whole corrected line", "why": "one short sentence"}], '
    '"notes": [{"line": 3, "rule": "FILE-1", "note": "one short sentence"}]}. '
    "Use the rule IDs from the rules. If nothing needs changing, reply "
    '{"changes": [], "notes": []}.\n\n'
    "THE RULES\n\n"
)

PROBLEMS = {
    "no-key": ("The AI formatting check needs a DeepSeek key, and none is set up "
               "on this Mac. Add one in Settings. The other checks work without it."),
    "busy": "DeepSeek was busy (rate limited), so the formatting check didn't run.",
    "key": ("DeepSeek didn't accept the saved key, so the formatting check didn't "
            "run. You can set a new key in Settings."),
    "credit": ("The DeepSeek account has no credit left, so the formatting check "
               "didn't run."),
    "offline": ("DeepSeek couldn't be reached, so the formatting check didn't run. "
                "This is usually the internet connection."),
    "timeout": "DeepSeek took too long to answer, so the formatting check didn't run.",
    "failed": ("Something went wrong contacting DeepSeek, so the formatting check "
               "didn't run."),
    "unexpected": ("DeepSeek sent back something unexpected, so the formatting check "
                   "didn't run."),
}


def problem_sentence(problem):
    if problem in PROBLEMS:
        return PROBLEMS[problem]
    if str(problem).startswith("http-"):
        return (f"DeepSeek returned an error ({problem[5:]}), so the formatting "
                "check didn't run.")
    return PROBLEMS["failed"]


# --- the knowledge base -----------------------------------------------------

def load_rules(path=None):
    """The rules file: its text, its version line, and each rule by ID.

    Returns {"text", "version", "rules": {id: {"text", "section"}}}.
    """
    path = path or RULES_PATH
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    m = VERSION_RE.search(text)
    version = m.group(1) if m else "unversioned"
    rules, section, current = {}, "", None
    for line in text.split("\n"):
        if line.startswith("## "):
            section = line[3:].strip()
            current = None
            continue
        m = RULE_RE.match(line)
        if m:
            current = m.group(1)
            rules[current] = {"text": m.group(2).strip(), "section": section}
        elif current and line.startswith("  ") and line.strip():
            rules[current]["text"] += " " + line.strip()
        elif not line.strip():
            current = None
    return {"text": text, "version": version, "rules": rules}


def build_prompt(rules):
    """The system prompt: the fixed reply format, then the rules file as it is."""
    return CONTRACT + rules["text"]


def numbered(docmap, max_chars=MAX_CHARS):
    """The chapter as sent: "<line number>\\t<line>", cut at max_chars."""
    out, size, truncated = [], 0, False
    for i, line in enumerate(docmap.lines):
        row = f"{i + 1}\t{line}"
        if size + len(row) + 1 > max_chars:
            truncated = True
            break
        out.append(row)
        size += len(row) + 1
    return "\n".join(out), truncated


# --- the wording guard ------------------------------------------------------

_URL_RE = re.compile(r"\]\(([^)\s]*)[^)]*\)|<(https?://[^>]+)>|(?<![(<])\b(https?://[^\s)>\]]+)")
_FOOTNOTE_RE = re.compile(r"\[\^([^\]]+)\]")
_BLOCKREF_RE = re.compile(r"\^([A-Za-z0-9][A-Za-z0-9-]*)\s*$")
_WIKILINK_RE = re.compile(r"!?\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")


def prose(line):
    """The words of a line, with markdown markup stripped and whitespace
    normalised. Two lines that differ only in formatting give the same string."""
    s = line
    s = re.sub(r"^\s{0,3}#{1,6}(?=\s|$|[^#])", "", s)             # heading marks
    s = re.sub(r"^(\s*>)+", "", s)                                  # quote / callout
    s = re.sub(r"^\s*\[![A-Za-z-]+\][+-]?", "", s)                  # callout type
    s = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", s)                 # list marker
    s = _BLOCKREF_RE.sub("", s)                                     # ^ref- marker
    s = re.sub(r"\[\^[^\]]+\]:?", "", s)                            # footnote labels
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)                 # pictures
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)                  # web links
    s = re.sub(r"\[([^\]]*)\]\{[^}]*\}", r"\1", s)                  # pandoc spans
    s = _WIKILINK_RE.sub(lambda m: m.group(2) if m.group(2) is not None
                         else m.group(1), s)                        # concept links
    s = re.sub(r"<[^>]+>", "", s)                                   # html tags
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", s)           # escapes
    s = s.replace("|", " ")                                         # table cells
    s = re.sub(r"[*_`~]", "", s)                                    # emphasis
    s = re.sub(r"(?:^|\s):?-{3,}:?(?=\s|$)", " ", s)                # table rule cells
    return " ".join(s.split())


def _urls(line):
    return sorted(next(g for g in m.groups() if g) for m in _URL_RE.finditer(line)
                  if any(m.groups()))


def _link_target(target):
    t = target.split("#", 1)[0].strip()
    if t.endswith(".md"):
        t = t[:-3]
    return t.rsplit("/", 1)[-1]


def guard(before, after, known_pages):
    """Why this change may not be made, or None if it is formatting only."""
    if "\n" in after or "\r" in after:
        return "it would split the line in two"
    if prose(before) != prose(after):
        return "it would change your wording"
    if _urls(before) != _urls(after):
        return "it would change a web address"
    if sorted(_FOOTNOTE_RE.findall(before)) != sorted(_FOOTNOTE_RE.findall(after)):
        return "it would change a footnote"
    if _BLOCKREF_RE.findall(before) != _BLOCKREF_RE.findall(after):
        return "it would change a reference marker"
    links_before = [m.group(1) for m in _WIKILINK_RE.finditer(before)]
    links_after = [m.group(1) for m in _WIKILINK_RE.finditer(after)]
    if len(links_before) != len(links_after):
        return "it would add or remove a concept link"
    for target in links_after:
        if target in links_before:
            continue
        if _link_target(target) not in known_pages:
            return f"it would link to “{target}”, which isn't a page in this book"
    return None


# --- the check --------------------------------------------------------------

def check(docmap, known_pages, rules=None, ask=None):
    """Ask DeepSeek for formatting fixes, and keep only the safe ones.

    known_pages: the exact names a [[link]] may point at (concept pages and
    chapters). ask: the request function, llm.ask_json unless a test gives one.
    Returns (findings, notes). findings is empty whenever anything went wrong,
    and notes say why in words the author can read.
    """
    ask = ask or llm.ask_json
    rules = rules or load_rules()
    if ask is llm.ask_json and not llm.have_key():
        return [], [problem_sentence("no-key")]

    text, truncated = numbered(docmap)
    answer, problem = ask(build_prompt(rules), text)
    if answer is None:
        return [], [problem_sentence(problem)]

    changes = answer.get("changes")
    if not isinstance(changes, list):
        return [], [problem_sentence("unexpected")]

    version = rules["version"]
    findings, dropped, seen = [], [], set()
    for item in changes[:MAX_PROPOSALS]:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        before = str(item.get("before", ""))
        after = str(item.get("after", ""))
        rule_id = str(item.get("rule", "")).strip().upper()
        why = " ".join(str(item.get("why", "")).split())
        i = n - 1
        if not 0 <= i < len(docmap.lines) or i in seen:
            continue
        line = docmap.lines[i]
        if after == line:
            continue
        if before != line:
            dropped.append((n, line, after, "the line it quoted isn't what is in "
                                             "your chapter"))
            continue
        if i in docmap.frontmatter or i in docmap.code:
            dropped.append((n, line, after, "frontmatter and code are left alone"))
            continue
        why_not = guard(line, after, known_pages)
        if why_not:
            dropped.append((n, line, after, why_not))
            continue
        seen.add(i)
        rule = rules["rules"].get(rule_id)
        if not rule:
            rule_id, rule = "OTHER", {"text": "Not one of the numbered rules.",
                                      "section": "Other formatting"}
        findings.append({
            "kind": "format",
            "group": f"format::{rule_id}",
            "group_label": f"{rule_id} ({rule['section']})",
            "line": i, "line_no": n, "start": 0, "end": len(line),
            "replacement": after,
            "before": "", "match": line, "after": "", "becomes": after,
            "rule": rule_id,
            "title": f"Formatting: {rule['section'].lower()}, line {n}",
            "explain": (why or "A formatting fix.") + f" Rule {rule_id}.",
            "detail_label": f"The rule, from the formatting rules (version {version})",
            "detail": rule["text"],
        })

    by_group = {}
    for f in findings:
        by_group.setdefault(f["group"], []).append(f)
    for group in by_group.values():
        for k, f in enumerate(group, 1):
            f["occurrence"], f["occurrence_total"] = k, len(group)

    notes = []
    if findings:
        notes.append(f"The AI formatting check (rules version {version}) proposed "
                     f"{len(findings)} formatting fix"
                     f"{'es' if len(findings) != 1 else ''}.")
    elif not dropped:
        notes.append(f"The AI formatting check (rules version {version}) found "
                     "nothing to fix.")
    for n, line, after, why_not in dropped[:MAX_NOTES_SHOWN]:
        notes.append(f"A proposed formatting change to line {n} was thrown away "
                     f"because {why_not}: “{_short(line)}” → “{_short(after)}”.")
    if len(dropped) > MAX_NOTES_SHOWN:
        notes.append(f"{len(dropped) - MAX_NOTES_SHOWN} more proposed changes were "
                     "thrown away for the same kinds of reason.")

    advice = answer.get("notes")
    if isinstance(advice, list):
        shown = 0
        for item in advice:
            if not isinstance(item, dict) or shown >= MAX_NOTES_SHOWN:
                continue
            note = " ".join(str(item.get("note", "")).split())
            if not note:
                continue
            where = item.get("line")
            rule_id = str(item.get("rule", "")).strip().upper()
            prefix = f"Line {where}" if isinstance(where, int) else "The chapter"
            notes.append(f"{prefix} ({rule_id or 'formatting'}), not changed: {note}")
            shown += 1
    if truncated:
        notes.append("The chapter was long, so only its first part was sent for the "
                     "formatting check.")
    return findings, notes


def _short(s, n=90):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"
