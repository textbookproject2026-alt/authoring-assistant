"""
Analysis 2 - term auto-linking.

Finds places where the chapter mentions the title of one of your concept pages
and offers to turn that mention into a link to the page.

Mentions are skipped when they sit in a heading, in code, inside a link that
already exists, or in the reference list. That, plus skipping anything already
linked, is what makes a second run quiet rather than duplicating work.
"""

import os
import posixpath
import re

from .mdmap import sentence_around

IGNORE_DIRS = {
    ".obsidian", ".git", ".github", ".trash", ".claude", "node_modules",
    "templates", "template", "scripts", "tests", "test", "assets", "backups",
    "attachments", "_site", "path-test", "admin", ".vscode",
}

IGNORE_STEMS = {
    "readme", "contributing", "license", "licence", "changelog", "index",
    "glossary", "qa", "todo", "home", "dashboard", "map of content", "moc",
}

CONCEPT_FOLDER_NAMES = ("definitions", "definition", "concepts", "concept",
                        "terms", "glossary", "key terms", "keyterms")

STOP_TITLES = {
    "the", "and", "for", "with", "this", "that", "notes", "note", "draft",
    "chapter", "chapters", "part", "introduction", "conclusion", "appendix",
    "references", "bibliography", "summary", "abstract", "overview",
}


def _aliases_from_frontmatter(text):
    """Read the 'aliases' field from a note's frontmatter, if it has one."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    block = text[3:end]
    out = []
    m = re.search(r"^\s*alias(?:es)?\s*:\s*(.*)$", block, re.M)
    if not m:
        return []
    inline = m.group(1).strip()
    if inline.startswith("["):
        out = [a.strip().strip("\"'") for a in inline.strip("[]").split(",")]
    elif inline:
        out = [inline.strip("\"'")]
    else:
        tail = block[m.end():]
        for line in tail.split("\n"):
            lm = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if not lm:
                break
            out.append(lm.group(1).strip("\"'"))
    return [a for a in out if a]


def discover_concept_pages(root, current_path, folder=None):
    """Find the notes whose titles we should look for in the chapter.

    Returns (pages, source_description). The pages are the ones in the
    vault's folder of definitions (the fullest folder with one of
    CONCEPT_FOLDER_NAMES), or in `folder` if one is given. A vault with no
    such folder has no concept pages, and ([], "") comes back.
    """
    root = os.path.abspath(root)
    search_root, described = root, None

    if folder:
        search_root = os.path.abspath(folder)
        described = os.path.relpath(search_root, root)
    else:
        best = None
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS
                           and not d.startswith(".")]
            name = os.path.basename(dirpath).casefold()
            if name in CONCEPT_FOLDER_NAMES:
                count = len([f for f in filenames if f.endswith(".md")])
                if count >= 2 and (best is None or count > best[1]):
                    best = (dirpath, count)
        if not best:
            # A book with no folder of concept pages has no concept pages:
            # its other chapters are never offered in their place.
            return [], ""
        search_root = best[0]
        described = os.path.relpath(search_root, root)

    pages = []
    current = os.path.abspath(current_path) if current_path else None
    for dirpath, dirnames, filenames in os.walk(search_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS
                       and not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            if current and os.path.abspath(full) == current:
                continue
            stem = fn[:-3]
            if stem.casefold() in IGNORE_STEMS or stem.casefold() in STOP_TITLES:
                continue
            if len(stem) < 3:
                continue
            titles = [stem]
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    head = fh.read(2000)
                titles.extend(_aliases_from_frontmatter(head))
            except OSError:
                pass
            pages.append({
                "title": stem,
                "path": full,
                "rel": os.path.relpath(full, root),
                "titles": titles,
            })
    return pages, (described or ".")


def concept_pages_in(paths, current, read_head, folder=None):
    """discover_concept_pages, for a book held as a list of file paths (with
    "/", from the top of the book) rather than a folder on this Mac.

    `read_head(path)` returns the start of a page's text, or None. The same
    folders are skipped, and the same folder of definitions is the only
    place concept pages come from.
    """
    def kept(path):
        return not any(d in IGNORE_DIRS or d.startswith(".")
                       for d in path.split("/")[:-1])

    notes = [p for p in sorted(paths) if p.endswith(".md") and kept(p)]
    search, described = "", None
    if folder:
        search = described = folder.strip("/")
    else:
        counts = {}
        for p in notes:
            here = posixpath.dirname(p)
            counts[here] = counts.get(here, 0) + 1
        best = None
        for here in sorted(counts):
            if posixpath.basename(here).casefold() in CONCEPT_FOLDER_NAMES \
                    and counts[here] >= 2 \
                    and (best is None or counts[here] > best[1]):
                best = (here, counts[here])
        if not best:
            return [], ""
        search = described = best[0]

    pages = []
    for p in notes:
        if search and not p.startswith(search + "/"):
            continue
        if p == current:
            continue
        stem = posixpath.basename(p)[:-3]
        if stem.casefold() in IGNORE_STEMS or stem.casefold() in STOP_TITLES:
            continue
        if len(stem) < 3:
            continue
        titles = [stem]
        head = read_head(p)
        if head:
            titles.extend(_aliases_from_frontmatter(head[:2000]))
        pages.append({"title": stem, "path": p, "rel": p, "titles": titles})
    return pages, (described or ".")


def _variants(title):
    """The written forms of a title we are willing to match."""
    forms = {title}
    if not title.endswith("s"):
        forms.add(title + "s")
        if re.search(r"(s|x|z|ch|sh)$", title):
            forms.add(title + "es")
        if title.endswith("y") and not re.search(r"[aeiou]y$", title):
            forms.add(title[:-1] + "ies")
    else:
        forms.add(title[:-1])
        if title.endswith("ies"):
            forms.add(title[:-3] + "y")
    return forms


def _build_matcher(pages):
    """One regex per concept page, longest titles first so that a page called
    'Critical Realism' wins over a page called 'Realism'."""
    matchers = []
    for page in pages:
        forms = set()
        for t in page["titles"]:
            t = t.strip()
            if len(t) < 3 or t.casefold() in STOP_TITLES:
                continue
            forms |= _variants(t)
        if not forms:
            continue
        ordered = sorted(forms, key=len, reverse=True)
        pattern = "|".join(re.escape(f) for f in ordered)
        matchers.append((
            page,
            re.compile(rf"(?<![\w\[|#-])({pattern})(?![\w\]|])", re.I),
            max(len(f) for f in forms),
        ))
    matchers.sort(key=lambda m: m[2], reverse=True)
    return matchers


def already_linked_titles(docmap, pages):
    """Concept pages that already have a link in the body of this chapter.

    Links inside a blockquote are ignored on purpose. Many chapters open with a
    "key concepts in this chapter" callout listing every concept page; that is a
    signpost, not a mention in the argument, so it should not stop us offering
    the real mentions further down.
    """
    linked = set()
    for page in pages:
        forms = [t.strip() for t in page["titles"] if t.strip()]
        if not forms:
            continue
        if page.get("link"):
            forms = [page["link"]]
        pattern = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
        rx = re.compile(rf"\[\[\s*(?:{pattern})\s*(?:\||#|\]\])", re.I)
        for lineno, line in enumerate(docmap.lines):
            if not docmap.is_prose_line(lineno) or docmap.in_references(lineno):
                continue
            if line.lstrip().startswith(">"):
                continue
            if rx.search(line):
                linked.add(page["title"])
                break
    return linked


def analyse(docmap, pages, first_mention_only=True):
    """Return (findings, notes)."""
    notes = []
    if not pages:
        return [], ["No concept pages were found, so there were no terms to link."]

    settled = already_linked_titles(docmap, pages) if first_mention_only else set()
    if settled:
        pages = [p for p in pages if p["title"] not in settled]
        notes.append(
            f"{len(settled)} concept page"
            f"{'s are' if len(settled) != 1 else ' is'} already linked in this "
            "chapter, so no further mentions were offered: "
            + ", ".join(sorted(settled)) + "."
        )
        if not pages:
            return [], notes

    matchers = _build_matcher(pages)
    hits = []
    for lineno, line in enumerate(docmap.lines):
        if not docmap.is_prose_line(lineno) or docmap.in_references(lineno):
            continue
        for page, rx, _ in matchers:
            for m in rx.finditer(line):
                hits.append((lineno, m.start(), m.end(), page, m.group(1)))

    # Longest match wins where two suggestions overlap on the same line.
    hits.sort(key=lambda h: (h[0], h[1], -(h[2] - h[1])))
    kept, occupied = [], {}
    already_linked = 0
    for lineno, start, end, page, text in hits:
        spans = occupied.setdefault(lineno, [])
        if any(start < e and s < end for s, e in spans):
            continue
        if not docmap.is_free(lineno, start, end):
            already_linked += 1
            continue
        spans.append((start, end))
        kept.append((lineno, start, end, page, text))

    totals = {}
    for _, _, _, page, _ in kept:
        totals[page["title"]] = totals.get(page["title"], 0) + 1

    findings, seen = [], {}
    skipped_later = 0
    for lineno, start, end, page, text in kept:
        title = page["title"]
        seen[title] = seen.get(title, 0) + 1
        if first_mention_only and seen[title] > 1:
            skipped_later += 1
            continue

        # Use the piped form only when the words on the page differ from the
        # target (a page may name its own: a glossary entry, glossary#Term).
        target = page.get("link", title)
        replacement = f"[[{target}]]" if text == target else f"[[{target}|{text}]]"
        before, match, after = sentence_around(docmap.lines[lineno], start, end)
        findings.append({
            "kind": "term",
            "group": f"term::{title}",
            "group_label": title,
            "line": lineno,
            "line_no": lineno + 1,
            "start": start,
            "end": end,
            "replacement": replacement,
            "before": before,
            "match": match,
            "after": after,
            "becomes": replacement,
            "occurrence": seen[title],
            "occurrence_total": totals[title],
            "title": f"Mention of “{title}”",
            "explain": f"Turn this into a link to your concept page “{title}”.",
            "detail_label": "Concept page",
            "detail": page["rel"],
        })

    if already_linked:
        notes.append(
            f"{already_linked} mention{'s' if already_linked != 1 else ''} of your "
            "concept pages already had links, so they were left alone."
        )
    if skipped_later:
        notes.append(
            f"{skipped_later} later mention{'s were' if skipped_later != 1 else ' was'} "
            "not offered because you asked for first mentions only."
        )
    return findings, notes


def expand_to_all_occurrences(docmap, pages, title, first_mention_only):
    """When the author says "yes to all", find the mentions that were held back
    because only first mentions were being offered.

    Every concept page is matched again, not just this one, so that the longest
    title still wins. Otherwise expanding "Realism" would start claiming the word
    inside "Critical Realism".
    """
    if not first_mention_only:
        return []
    if not any(p["title"] == title for p in pages):
        return []
    findings, _ = analyse(docmap, pages, first_mention_only=False)
    return [f for f in findings
            if f["group_label"] == title and f["occurrence"] > 1]
