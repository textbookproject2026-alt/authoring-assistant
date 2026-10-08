"""
Analysis 3 - glossary extraction.

Works without any AI service. Three ordinary signals are used:

  1. terms introduced with a definition ("X is defined as", "by X we mean")
  2. terms the author put in bold the first time they used them
  3. capitalised multi-word terms that come up more than once

An optional DeepSeek pass can suggest extra terms, but it is never required. If
there is no key, or the service is slow, busy or unavailable, the deterministic
result is used instead and the interface says so.

Approved entries are added to a single glossary.md. Existing entries are never
rewritten and never duplicated.
"""

import os
import re

from .mdmap import sentence_around, split_lines, join_lines

ARTICLES = r"(?:the|a|an|this|that|these|those|such|其)\s+"

DEFINITIONAL = [
    (re.compile(rf"\b(?:{ARTICLES})?(?P<t>[A-Z][\w’'-]*(?:\s+[\w’'-]+){{0,3}})\s+"
                r"(?:is|are)\s+(?:defined|understood|conceived|conceptualised|"
                r"conceptualized|described)\s+as\b"), "is defined as"),
    (re.compile(rf"\b(?:{ARTICLES})?(?P<t>[A-Za-z][\w’'-]*(?:\s+[\w’'-]+){{0,3}})\s+"
                r"refers\s+to\b"), "refers to"),
    (re.compile(rf"\b(?:{ARTICLES})?(?P<t>[A-Za-z][\w’'-]*(?:\s+[\w’'-]+){{0,3}})\s+"
                r"(?:denotes|designates|signifies)\b"), "denotes"),
    (re.compile(r"\b(?:[Ww]e|I)\s+(?:define|term|call)\s+"
                r"(?P<t>[A-Za-z][\w’'-]*(?:\s+[\w’'-]+){0,3})\s+(?:as|to\s+be)\b"),
     "we define ... as"),
    (re.compile(r"\b[Bb]y\s+(?P<t>[A-Za-z][\w’'-]*(?:\s+[\w’'-]+){0,3})\s+"
                r"(?:[Ww]e|I)\s+mean\b"), "by ... we mean"),
    # Only trust "the term X" when the author marked where X ends, with quotes
    # or emphasis. Unquoted, there is no reliable way to tell how far it runs.
    (re.compile(r"\b[Tt]he\s+term\s+(?P<q>[“\"'‘]|\*\*|\*|_)"
                r"(?P<t>[A-Za-z][\w’'\s-]{2,58}?)"
                r"(?:[”\"'’]|\*\*|\*|_)"), "the term"),
    (re.compile(rf"\b(?:{ARTICLES})?(?P<t>[A-Z][\w’'-]*(?:\s+[\w’'-]+){{0,3}})\s+"
                r"is\s+the\s+term\s+for\b"), "is the term for"),
]

ABBREV_RE = re.compile(r"(?P<t>(?:[A-Za-z][\w’'-]*\s+){1,5}[A-Za-z][\w’'-]*)\s+\((?P<a>[A-Z]{2,6})\)")

BOLD_RE = re.compile(r"\*\*(?P<t>[^*\n]{3,70})\*\*")

CONNECTOR_WORDS = {"of", "the", "and", "in", "for", "to", "on", "as", "by"}

COMMON_LEADERS = {
    "the", "this", "that", "these", "those", "such", "it", "there", "here",
    "chapter", "chapters", "section", "sections", "part", "figure", "table",
    "box", "appendix", "page", "volume", "book", "however", "therefore",
    "moreover", "nevertheless", "first", "second", "third", "finally", "both",
    "what", "when", "where", "while", "although", "because", "since", "if",
    "his", "her", "their", "our", "its", "one", "two", "three", "some", "many",
    "most", "each", "every", "any", "all", "no", "not", "more", "less",
}

STOP_TERMS = {
    "critical realism",  # replaced dynamically by concept pages, kept as a hint
}


def _strip_markdown(s):
    s = re.sub(r"!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\s*\^[A-Za-z0-9-]+\s*$", "", s)
    # Emphasis markers left unpaired because we sliced into the middle of a
    # sentence would otherwise show up as stray asterisks in the glossary.
    s = s.replace("**", "")
    s = re.sub(r"(?<!\w)[*_](?!\w)", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _clean_term(t):
    t = _strip_markdown(t).strip(" ,.;:—–-“”\"'()")
    t = re.sub(r"^(?:the|a|an|this|that|these|those|such)\s+", "", t, flags=re.I)
    return t.strip()


def _plausible_term(t, author_names):
    if not t or len(t) < 4 or len(t) > 60:
        return False
    words = t.split()
    if len(words) > 5:
        return False
    if words[0].casefold() in COMMON_LEADERS:
        return False
    if not re.search(r"[A-Za-z]{3}", t):
        return False
    if re.search(r"\d{4}", t):
        return False
    if any(w.strip(".,").casefold() in author_names for w in words):
        return False
    if t.casefold() in COMMON_LEADERS:
        return False
    return True


# Words that never start a term: a capitalised run led by one ("At Time") is a
# fragment of a sentence, not a name for something.
FUNCTION_WORDS = {
    "at", "in", "on", "of", "for", "to", "by", "as", "with", "from", "into",
    "after", "before", "during", "since", "until", "about", "between", "within",
    "and", "or", "but", "nor", "so", "yet", "if", "when", "while", "where",
    "because", "although", "though", "unless", "than", "then", "thus", "hence",
    "is", "are", "was", "were", "be", "been", "has", "have", "had", "do", "does",
}
# Words that make a phrase a clause, not a term ("what you should do").
CLAUSE_WORDS = {"you", "your", "we", "our", "us", "i", "my", "what", "which",
                "should", "must", "can", "could", "would", "will", "shall", "may",
                "might", "this", "that", "these", "those", "it", "its"}


def non_term(term, line, start, kind):
    """Why `term` (found at line[start:]) is obviously not a glossary term, or None.

    Fragments split off a hyphenated compound ("Based Modeling" from
    "Agent-Based Modeling"), runs led by a function word ("At Time"),
    possessives ("Archer's"), bold labels (a colon, dash or slash in them:
    "Yes: structural availability", "Time/space"), and run-in phrases (a comma,
    or a clause word: "Practically, what you should do").
    """
    words = term.split()
    if start > 0 and line[start - 1] in "-‐–" and line[start - 2:start - 1].isalpha():
        return "a fragment of a hyphenated term"
    if words and words[0].casefold() in FUNCTION_WORDS:
        return f"starts with “{words[0]}”"
    if re.search(r"[’']s$", term):
        return "a possessive"
    if re.search(r"[:—–]|\s-\s", term):
        return "a label, not a term"
    if "," in term or any(w.strip(".,;").casefold() in CLAUSE_WORDS for w in words[1:]):
        return "a phrase, not a term"
    if kind == "bold" and len(words) > 4:
        return "too long for a term"
    # "Beach and Pedersen (2013)", "(Gerber and Green, 2012)": who wrote something.
    if re.match(r"\s*(?:,\s*|\(\s*)(?:1[5-9]|20)\d\d\b", line[start + len(term):]):
        return "a citation's authors"
    return None


def sentence_initial(line, start):
    """True when line[start:] begins a sentence (start of the line, or after . ! ? :)."""
    return not line[:start].strip() or bool(re.search(r"[.!?:]\s+$", line[:start]))


def _sentence_for(docmap, lineno, start, end, limit=320):
    """The sentence that introduces the term, tidied into a usable definition.

    If the term appears late in a very long sentence, the definition starts at
    the term rather than opening with an ellipsis in the middle of a clause.
    """
    _, sentence, _ = sentence_around(docmap.lines[lineno], start, end, budget=None)
    term_text = docmap.lines[lineno][start:end]
    pos = sentence.find(term_text)
    if pos > 100:
        sentence = sentence[pos:]
    sentence = _strip_markdown(sentence).strip()
    if len(sentence) > limit:
        cut = sentence.rfind(" ", 0, limit)
        sentence = sentence[: cut if cut > limit // 2 else limit].rstrip(" ,;:") + "…"
    return sentence


def _capitalised_runs(line):
    """Runs of two to four capitalised words, allowing small joining words."""
    out = []
    # A word may be a hyphenated compound, whole: "Agent-Based", "Multi-Sited".
    word = r"[A-Z][a-zà-ÿ’']+(?:-[A-Za-zà-ÿ’']+)*"
    for m in re.finditer(
        rf"(?<![\w-]){word}(?:\s+(?:of|the|and|in|for|to|on|as|by)\s+{word}"
        rf"|\s+{word}){{1,3}}\b",
        line,
    ):
        out.append((m.start(), m.end(), m.group(0)))
    return out


def existing_glossary_terms(glossary_path):
    """Terms already in glossary.md, so we never offer or add them twice."""
    if not glossary_path or not os.path.exists(glossary_path):
        return set()
    try:
        with open(glossary_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return set()
    return glossary_terms_in(text)


def glossary_terms_in(text):
    """The terms a glossary's text already has."""
    terms = set()
    if not text:
        return terms
    for m in re.finditer(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", text, re.M):
        terms.add(_strip_markdown(m.group(1)).casefold())
    for m in re.finditer(r"^\s*[-*+]\s*\*\*(.+?)\*\*", text, re.M):
        terms.add(_strip_markdown(m.group(1)).casefold())
    return terms


def glossary_headings(text):
    """The terms a glossary has as headings ("## Term"), as written: the entries a
    link can point at (glossary#Term opens at the entry on the book's site)."""
    out = []
    in_fence = False
    for line in (text or "").splitlines():
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", line):
            in_fence = not in_fence
            continue
        m = None if in_fence else re.match(r"^\s{0,3}#{2,4}\s+(.+?)\s*#*\s*$", line)
        if m:
            out.append(_strip_markdown(m.group(1)))
    return out


def link_pages(headings, target, concept_titles=()):
    """The glossary's entries as pages for terms.analyse, each linking to its
    entry: [[<target>#Term|words]]. A term that is also a concept page's title
    is left to the concept link."""
    concepts = {t.casefold() for t in concept_titles}
    return [{"title": h, "titles": [h], "link": f"{target}#{h}",
             "rel": f"{target}.md", "path": f"{target}.md"}
            for h in headings if h.casefold() not in concepts and len(h) >= 3]


def link_findings(docmap, pages, first_mention_only=True):
    """Mentions of glossary terms, each offered as a link to its entry
    (terms.analyse, worded for the glossary). Returns (findings, notes)."""
    from . import terms
    if not pages:
        return [], []
    found, _ = terms.analyse(docmap, pages, first_mention_only)
    for f in found:
        term = f["group_label"]
        f.update(
            group=f"glossary-link::{term.casefold()}",
            title=f"Mention of “{term}”",
            explain=f"Link this to “{term}” in your glossary. On the book's site, "
                    "pointing at the link shows the entry.",
            detail_label="Glossary entry",
            detail=term,
        )
    return found, []


def link_for(term, target, text):
    """The link a glossary mention becomes."""
    return f"[[{target}#{term}|{text}]]"


def analyse(docmap, existing_terms=(), concept_titles=(), author_names=(),
            min_repeats=2, removed=None):
    """Deterministic glossary candidates. Returns (findings, notes).

    Obvious non-terms are never offered (non_term). `removed`, a list, gets each
    one as (term, why), once per term: what a dry run reports.
    """
    existing = {t.casefold() for t in existing_terms}
    concepts = {t.casefold() for t in concept_titles}
    authors = {a.casefold() for a in author_names}

    candidates = {}   # key -> record

    dropped = set()

    def drop(term, why):
        if removed is not None and term.casefold() not in dropped:
            dropped.add(term.casefold())
            removed.append((term, why))

    def add(term, kind, reason, lineno, start, end, weight):
        # A dichotomy written as a pair ("Structure/Agency", "Time/space"): each
        # side is a term of its own, offered separately.
        pair = re.fullmatch(r"\s*([^/]+?)\s*/\s*([^/]+?)\s*", _strip_markdown(term))
        if pair:
            line = docmap.lines[lineno]
            for side in pair.groups():
                at = line.find(side, start)
                at = start if at < 0 else at
                add(side, kind, reason, lineno, at, at + len(side), weight)
            return
        raw = term
        term = _clean_term(term)
        if not _plausible_term(term, authors):
            return
        why = non_term(term, docmap.lines[lineno], start + (len(raw) - len(raw.lstrip())), kind)
        if why:
            return drop(term, why)
        key = term.casefold()
        if key in existing or key in concepts:
            return
        rec = candidates.get(key)
        if rec is None:
            rec = {
                "term": term, "kind": kind, "reason": reason, "line": lineno,
                "start": start, "end": end, "weight": weight, "count": 0,
            }
            candidates[key] = rec
        rec["count"] += 1
        if weight > rec["weight"]:
            rec.update(kind=kind, reason=reason, line=lineno,
                       start=start, end=end, weight=weight)

    repeats = {}
    for lineno, line in enumerate(docmap.lines):
        if not docmap.is_prose_line(lineno) or docmap.in_references(lineno):
            continue

        for rx, label in DEFINITIONAL:
            for m in rx.finditer(line):
                add(m.group("t"), "definition", f"introduced with “{label}”",
                    lineno, m.start("t"), m.end("t"), 3)

        # A term introduced with its abbreviation: "Agent-based modeling (ABM)",
        # the abbreviation being the phrase's initials (hyphenated parts count).
        for m in ABBREV_RE.finditer(line):
            words = m.group("t").split()
            for n in range(min(len(words), 6), 1, -1):
                phrase = words[-n:]
                initials = "".join(p[0] for w in phrase for p in w.split("-") if p)
                if initials.upper() == m.group("a"):
                    t = " ".join(phrase)
                    at = m.start("t") + len(m.group("t")) - len(t)
                    add(t, "abbreviation", f"introduced with its abbreviation ({m.group('a')})",
                        lineno, at, at + len(t), 3)
                    break

        for m in BOLD_RE.finditer(line):
            raw = m.group("t").strip()
            # A bold run ending in a full stop is a run-in subheading, not a term.
            if raw.endswith("."):
                continue
            add(raw, "bold", "put in bold the first time it appears",
                lineno, m.start("t"), m.end("t"), 2)

        for start, end, text in _capitalised_runs(line):
            # A sentence's first word is capitalised whatever it is: a leading
            # function word ("At", "For", "But", "The") there isn't part of the term.
            first = text.split()[0]
            if sentence_initial(line, start) and first.casefold() in (FUNCTION_WORDS | COMMON_LEADERS):
                rest = text[len(first):].lstrip()
                if len(rest.split()) < 2:
                    continue
                start, text = start + len(text) - len(rest), rest
            cleaned = _clean_term(text)
            # Once a leading article is dropped the phrase must still be more
            # than one word, or "The Actual" would be offered as "Actual".
            if len(cleaned.split()) < 2:
                continue
            repeats.setdefault(cleaned.casefold(), []).append(
                (lineno, start, end, text)
            )

    for key, places in repeats.items():
        if len(places) < min_repeats:
            continue
        lineno, start, end, text = places[0]
        add(text, "repeated",
            f"a capitalised term used {len(places)} times in this chapter",
            lineno, start, end, 1)
        if key in candidates:
            candidates[key]["count"] = max(candidates[key]["count"], len(places))

    findings = []
    order = {"definition": 0, "abbreviation": 0, "bold": 1, "repeated": 2}
    for rec in sorted(candidates.values(),
                      key=lambda r: (order[r["kind"]], -r["count"],
                                     r["term"].casefold())):
        sentence = _sentence_for(docmap, rec["line"], rec["start"], rec["end"])
        before, match, after = sentence_around(
            docmap.lines[rec["line"]], rec["start"], rec["end"], budget=420
        )
        findings.append({
            "kind": "glossary",
            "group": f"glossary::{rec['term'].casefold()}",
            "group_label": rec["term"],
            "line": rec["line"],
            "line_no": rec["line"] + 1,
            "start": rec["start"],
            "end": rec["end"],
            "term": rec["term"],
            "definition": sentence,
            "source": "chapter",
            "before": _strip_markdown(before),
            "match": match,
            "after": _strip_markdown(after),
            "becomes": "",
            "occurrence": 1,
            "occurrence_total": 1,
            "title": f"Glossary entry for “{rec['term']}”",
            "explain": f"Add “{rec['term']}” to your glossary. "
                       f"Found because it is {rec['reason']}.",
            "detail_label": "Suggested wording, taken from your chapter",
            "detail": sentence,
        })

    notes = []
    if existing:
        n = len(existing)
        notes.append(
            f"{n} term{'s' if n != 1 else ''} already in your glossary "
            f"{'were' if n != 1 else 'was'} left out of these suggestions."
        )
    return findings, notes


# --- writing the glossary ----------------------------------------------------

def _entry_block(term, definition, source_name):
    body = definition.strip()
    if body:
        body = body[0].upper() + body[1:]
    if body and not body.endswith((".", "!", "?", ":")):
        body += "."
    if source_name:
        body = f"{body} (First used in {source_name}.)" if body else \
               f"First used in {source_name}."
    return [f"## {term}", "", body, ""]


def plan_glossary(glossary_path, new_entries, source_name=""):
    """Work out what glossary.md will look like, without writing anything.

    Existing lines are copied through untouched; new entries are spliced into
    alphabetical position between them.
    """
    original = None
    if glossary_path and os.path.exists(glossary_path):
        with open(glossary_path, "r", encoding="utf-8", errors="replace") as fh:
            original = fh.read()
    return plan_glossary_text(original, new_entries, source_name)


def plan_glossary_text(original, new_entries, source_name=""):
    """plan_glossary, for a glossary held as text. `original` is None when
    there is no glossary yet."""
    if original is not None:
        lines, newline, trailing = split_lines(original)
    else:
        original = ""
        lines, newline, trailing = ["# Glossary", ""], "\n", True

    heads = []   # (line index, term)
    in_fence = False
    for i, line in enumerate(lines):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", line)
        if m:
            heads.append((i, _strip_markdown(m.group(1))))

    have = {t.casefold() for _, t in heads}
    additions = []
    for term, definition in new_entries:
        if term.casefold() in have:
            continue
        have.add(term.casefold())
        additions.append((term, definition))

    if not additions:
        return original, original, [], lines, newline, trailing

    # Decide an insertion point for each new entry, keeping A-Z order.
    inserts = {}
    for term, definition in additions:
        target = len(lines)
        for idx, existing_term in heads:
            if existing_term.casefold() > term.casefold():
                target = idx
                break
        inserts.setdefault(target, []).append((term, definition))
        heads.append((target, term))
        heads.sort(key=lambda h: (h[0], h[1].casefold()))

    out, added_ranges = [], []
    for i, line in enumerate(lines):
        for term, definition in sorted(inserts.get(i, []),
                                       key=lambda p: p[0].casefold()):
            block = _entry_block(term, definition, source_name)
            added_ranges.append((len(out), len(out) + len(block), term))
            out.extend(block)
        out.append(line)
    for term, definition in sorted(inserts.get(len(lines), []),
                                   key=lambda p: p[0].casefold()):
        if out and out[-1].strip():
            out.append("")
        block = _entry_block(term, definition, source_name)
        added_ranges.append((len(out), len(out) + len(block), term))
        out.extend(block)

    # Entry blocks end in a blank line so entries are separated, but the file
    # itself must end with one newline after the last content line, or
    # markdownlint's MD012 flags the blank line an entry sorting last leaves
    # behind - and it would come back on the next append.
    while out and not out[-1].strip():
        out.pop()
    new_text = join_lines(out, newline, True)
    return original, new_text, added_ranges, out, newline, trailing
