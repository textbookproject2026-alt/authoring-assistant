"""
Analysis 1 - reference auto-linking.

Finds citations in the running text, such as "(Bhaskar, 1975)" or
"Bhaskar (1975)", matches them against the entries in the chapter's own
References section, and offers to turn each one into a link that jumps to that
entry.

A citation is only ever offered if we found the matching entry. Citations with no
matching entry are reported back to the author as something to check, which is
useful in its own right, but nothing is invented or guessed.
"""

import re
import unicodedata

from .edits import Edit
from .mdmap import sentence_around

YEAR = r"(?:1[5-9]\d{2}|20\d{2})"
YEAR_RE = re.compile(rf"(?<![\d.])({YEAR})([a-z])?(?:/({YEAR}))?(?![\d])")
PARENS_RE = re.compile(r"\(([^()]{2,300})\)")

PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "di", "da",
             "du", "la", "le", "el", "ten", "ter", "st"}

CONNECTORS = {"and", "&", "et", "al", "eds", "ed", "trans", "with", "in"}

# Words that can open a parenthetical citation without being part of the name.
LEAD_IN_RE = re.compile(
    r"^\s*(?:see\s+also|see|e\.g\.,?|cf\.,?|also|compare|following|after|"
    r"as\s+in|for\s+example,?|for\s+instance,?)\s+",
    re.I,
)

NARRATIVE_RE = re.compile(
    rf"(?<![\w\[])"
    rf"(?P<authors>"
    rf"(?:(?:van|von|de|der|del|di|da|du|la|le|el)\s+)?"
    rf"[A-Z][\w’'À-ɏ-]+"
    rf"(?:\s*(?:,\s*)?(?:and|&)\s+(?:(?:van|von|de|der|del|di|da|du|la|le|el)\s+)?"
    rf"[A-Z][\w’'À-ɏ-]+)?"
    rf"(?:\s+et\s+al\.?)?"
    rf")"
    rf"\s+\((?P<inner>{YEAR}[a-z]?(?:/{YEAR})?[^()]{{0,40}})\)"
)


def fold(s):
    """Compare names without being defeated by accents or curly apostrophes."""
    s = s.replace("’", "'")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().strip()


def slugify(s):
    s = fold(s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "ref"


def surnames_from_authors(chunk):
    """Pull the family names out of an author list in either direction:
    'Bhaskar, R.' and 'Berger, P. L., & Luckmann, T.' both work."""
    chunk = re.sub(r"\(.*?\)", " ", chunk)
    chunk = chunk.replace("&", " & ")
    tokens = re.split(r"[\s,;]+", chunk)
    names, i = [], 0
    while i < len(tokens):
        tok = tokens[i].strip(".")
        low = tok.casefold()
        if not tok or low in CONNECTORS or tok == "&":
            i += 1
            continue
        # An initial such as "R." or "P. L." is not a family name.
        if len(tok) <= 1 or re.fullmatch(r"[A-Z]", tok):
            i += 1
            continue
        if low in PARTICLES and i + 1 < len(tokens):
            nxt = tokens[i + 1].strip(".")
            if nxt and not re.fullmatch(r"[A-Z]", nxt):
                names.append(f"{tok} {nxt}")
                i += 2
                continue
        if re.match(r"^[A-ZÀ-ɏ]", tok) or low in PARTICLES:
            names.append(tok)
        i += 1
    return names


class RefEntry:
    def __init__(self, line, raw, surnames, years, anchor, existing_anchor):
        self.line = line
        self.raw = raw
        self.surnames = surnames
        self.years = years              # every year the entry can be cited by
        self.anchor = anchor
        self.existing_anchor = existing_anchor

    @property
    def primary(self):
        return self.surnames[0] if self.surnames else ""

    def display(self):
        text = re.sub(r"\s*\^[A-Za-z0-9-]+\s*$", "", self.raw.strip())
        text = re.sub(r"^\s*[-*+]\s+", "", text)
        return text


BLOCK_ID_RE = re.compile(r"\s\^([A-Za-z0-9][A-Za-z0-9-]*)\s*$")
HTML_ANCHOR_RE = re.compile(r'<a\s+id="([^"]+)"\s*>\s*</a>')


def parse_reference_list(docmap):
    """Read the References section into structured entries."""
    entries = []
    used = set()
    for lineno in docmap.reference_entry_lines():
        raw = docmap.lines[lineno]
        stripped = re.sub(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+", "", raw)

        m = YEAR_RE.search(stripped)
        if not m:
            continue
        years = {m.group(1) + (m.group(2) or "")}
        years.add(m.group(1))
        if m.group(3):
            years.add(m.group(3))
        # "(Original work published 1867)" style reprints.
        for extra in re.finditer(
            rf"original\s+work\s+published\s+({YEAR})", stripped, re.I
        ):
            years.add(extra.group(1))

        authors_chunk = stripped[: m.start()]
        surnames = surnames_from_authors(authors_chunk)
        if not surnames:
            continue

        existing = None
        bm = BLOCK_ID_RE.search(raw)
        if bm:
            existing = bm.group(1)
        else:
            hm = HTML_ANCHOR_RE.search(raw)
            if hm:
                existing = hm.group(1)

        base = existing or f"ref-{slugify(surnames[0])}-{m.group(1)}{m.group(2) or ''}"
        anchor = base
        n = 2
        while anchor in used:
            anchor = f"{base}-{n}"
            n += 1
        used.add(anchor)

        entries.append(RefEntry(lineno, raw, surnames, years, anchor, existing))
    return entries


def build_index(entries):
    """Look-up table from (family name, year) to the entries that match."""
    index = {}
    for e in entries:
        for surname in e.surnames:
            for year in e.years:
                index.setdefault((fold(surname), year), []).append(e)
    return index


def _lookup(index, surname, year):
    """Return a single entry, or None if absent or genuinely ambiguous."""
    hits = index.get((fold(surname), year), [])
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        unique = {h.anchor for h in hits}
        if len(unique) == 1:
            return hits[0]
    return None


class Citation:
    def __init__(self, line, start, end, text, surname, year, entry=None):
        self.line = line
        self.start = start
        self.end = end
        self.text = text
        self.surname = surname
        self.year = year
        self.entry = entry


def _scan_parenthetical(docmap, index, lineno, line):
    """Handle '(Bhaskar, 1975)', '(Bhaskar, 1979; Sayer, 2000)',
    '(Bhaskar, 1975, 1979)' and '(Foucault, 1977; 1980)'."""
    found = []
    for group in PARENS_RE.finditer(line):
        inner = group.group(1)
        inner_start = group.start(1)
        if not YEAR_RE.search(inner):
            continue

        offset = 0
        last_surname = None
        for part in re.split(r";", inner):
            part_start = inner_start + offset
            offset += len(part) + 1

            years = list(YEAR_RE.finditer(part))
            if not years:
                last_surname = None
                continue

            head = part[: years[0].start()]
            lead = LEAD_IN_RE.match(head)
            if lead:
                head = head[lead.end():]
                head_offset = lead.end()
            else:
                head_offset = 0
            head_stripped = head.rstrip()
            head_stripped = re.sub(r"[,\s]+$", "", head_stripped)

            surnames = surnames_from_authors(head_stripped) if head_stripped.strip() else []
            if surnames:
                surname = surnames[0]
                last_surname = surname
                # Span starts at the family name, not at any "see also" lead-in.
                lead_ws = len(head) - len(head.lstrip())
                span_start = part_start + head_offset + lead_ws
            elif head_stripped.strip():
                # Text before the year that is not a name: not a citation.
                last_surname = None
                continue
            else:
                surname = last_surname
                span_start = part_start + years[0].start()
                if not surname:
                    continue

            for idx, ym in enumerate(years):
                year = ym.group(1) + (ym.group(2) or "")
                entry = _lookup(index, surname, year) or _lookup(
                    index, surname, ym.group(1)
                )
                if idx == 0:
                    s, e = span_start, part_start + ym.end()
                else:
                    s, e = part_start + ym.start(), part_start + ym.end()
                if e <= s:
                    continue
                found.append(
                    Citation(lineno, s, e, line[s:e], surname, year, entry)
                )
    return found


def _scan_narrative(docmap, index, lineno, line):
    """Handle 'Bhaskar (1975)' and 'Bhaskar (1975, p. 23)'."""
    found = []
    for m in NARRATIVE_RE.finditer(line):
        # Skip anything already inside a parenthetical citation group.
        if line[: m.start()].count("(") - line[: m.start()].count(")") > 0:
            continue
        inner = m.group("inner")
        ym = YEAR_RE.search(inner)
        if not ym:
            continue
        year = ym.group(1) + (ym.group(2) or "")
        surnames = surnames_from_authors(m.group("authors"))
        if not surnames:
            continue
        surname = surnames[0]
        entry = _lookup(index, surname, year) or _lookup(index, surname, ym.group(1))

        if ym.group(0) == inner.strip():
            # The brackets hold nothing but the year: link the whole citation.
            s, e = m.start(), m.end()
        else:
            # There is a page number too: link only the year, keeping brackets valid.
            inner_pos = m.start("inner")
            s, e = inner_pos + ym.start(), inner_pos + ym.end()
        found.append(Citation(lineno, s, e, line[s:e], surname, year, entry))
    return found


def analyse(docmap, anchor_style="obsidian"):
    """Return (findings, anchor_edits_by_group, notes)."""
    entries = parse_reference_list(docmap)
    notes = []
    if docmap.refs_start is None:
        return [], {}, ["This chapter has no References section, so citations "
                        "could not be linked to anything."]
    if not entries:
        notes.append("The References section was found but no entries in it could "
                     "be read, so no citation links were suggested.")
        return [], {}, notes

    index = build_index(entries)

    citations = []
    for lineno, line in enumerate(docmap.lines):
        if not docmap.is_prose_line(lineno) or docmap.in_references(lineno):
            continue
        citations.extend(_scan_parenthetical(docmap, index, lineno, line))
        citations.extend(_scan_narrative(docmap, index, lineno, line))

    # Drop anything sitting inside code, an existing link, or another citation.
    citations.sort(key=lambda c: (c.line, c.start, -c.end))
    kept, occupied = [], {}
    already_linked = 0
    for c in citations:
        spans = occupied.setdefault(c.line, [])
        if any(c.start < e and s < c.end for s, e in spans):
            continue
        if not docmap.is_free(c.line, c.start, c.end):
            already_linked += 1
            continue
        spans.append((c.start, c.end))
        kept.append(c)

    unmatched = {}
    findings, anchor_edits = [], {}
    counts = {}
    for c in kept:
        if not c.entry:
            unmatched.setdefault(f"{c.surname}, {c.year}", 0)
            unmatched[f"{c.surname}, {c.year}"] += 1
            continue
        counts[c.entry.anchor] = counts.get(c.entry.anchor, 0) + 1

    seen = {}
    for c in kept:
        if not c.entry:
            continue
        entry = c.entry
        seen[entry.anchor] = seen.get(entry.anchor, 0) + 1
        target = f"#^{entry.anchor}" if anchor_style == "obsidian" else f"#{entry.anchor}"
        replacement = f"[{c.text}]({target})"
        before, match, after = sentence_around(docmap.lines[c.line], c.start, c.end)
        findings.append({
            "kind": "reference",
            "group": entry.anchor,
            "group_label": f"{entry.primary} ({c.year})",
            "line": c.line,
            "line_no": c.line + 1,
            "start": c.start,
            "end": c.end,
            "replacement": replacement,
            "before": before,
            "match": match,
            "after": after,
            "becomes": replacement,
            "occurrence": seen[entry.anchor],
            "occurrence_total": counts[entry.anchor],
            "title": f"Citation of {entry.primary} ({c.year})",
            "explain": "Turn this citation into a link that jumps straight to the "
                       "matching entry in your reference list.",
            "detail_label": "Matching entry in your reference list",
            "detail": entry.display(),
        })

        if entry.anchor not in anchor_edits and not entry.existing_anchor:
            raw = docmap.lines[entry.line]
            if anchor_style == "obsidian":
                # Append the block id, preserving any trailing whitespace.
                stripped = raw.rstrip()
                anchor_edits[entry.anchor] = Edit(
                    entry.line, len(stripped), len(raw),
                    f" ^{entry.anchor}", origin=f"anchor for {entry.anchor}",
                )
            else:
                lead = len(raw) - len(raw.lstrip())
                bullet = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", raw)
                pos = bullet.end() if bullet else lead
                anchor_edits[entry.anchor] = Edit(
                    entry.line, pos, pos,
                    f'<a id="{entry.anchor}"></a>',
                    origin=f"anchor for {entry.anchor}",
                )

    if already_linked:
        notes.append(
            f"{already_linked} citation{'s' if already_linked != 1 else ''} "
            "already had a link, so they were left alone."
        )
    if unmatched:
        listed = ", ".join(sorted(unmatched)[:8])
        more = "" if len(unmatched) <= 8 else f", and {len(unmatched) - 8} more"
        notes.append(
            "These citations have no matching entry in your reference list, so they "
            f"were not offered: {listed}{more}. It may be worth checking them."
        )
    return findings, anchor_edits, notes
