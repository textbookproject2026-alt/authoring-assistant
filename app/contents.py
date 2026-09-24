"""
A new chapter's line on the book's front page.

A chapter brought in from Word reaches the book, its search and its graph, but
readers find chapters from the front page's "Contents" list, and before this
the author had to add that line by hand. Sending a NEW chapter to the drafts
area now adds it, in the same commit, and the author sees the line first.

The same rules as every other edit here:

  * Only one line is added. Every other line of index.md goes back byte for
    byte, line endings included.
  * Never guessed. No "## Contents" heading, or the chapter already linked
    under it: nothing is added, and the author is told why.

The line's shape is the template's own (textbook-template/scripts/seed-index.md):
    - **[[chapters/Chapter 3|Chapter 3: Reality and the Problem]]**
book-requests' provisioning calls add_line() too, so a book set up from a
request and a chapter added later read the same.
"""

import re

HEADING = re.compile(r"^##\s+Contents\s*$", re.IGNORECASE)
SECTION_END = re.compile(r"^(#{1,2}\s|---\s*$)")
ITEM = re.compile(r"^[-*]\s")


def chapter_title(text, md_name):
    """The chapter's first "# Heading", else its file name without .md."""
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            title = re.sub(r"\*\*|__|[*_`]", "", m.group(1))
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                return title
    name = md_name.rsplit("/", 1)[-1]
    return re.sub(r"\.(md|markdown)$", "", name, flags=re.IGNORECASE) or "Untitled chapter"


def link_target(chapter_path):
    """chapters/Chapter 3.md -> chapters/Chapter 3 (a wikilink names no .md)."""
    return re.sub(r"\.(md|markdown)$", "", chapter_path, flags=re.IGNORECASE)


def line_for(chapter_path, title):
    # "|" ends a wikilink's target and "]]" the link: neither may be in the label.
    label = title.replace("|", "-").replace("]]", "] ]").replace("[[", "[ [")
    return f"- **[[{link_target(chapter_path)}|{label}]]**"


def add_line(index_text, chapter_path, title):
    """(new index text, None), or (None, why in the author's words).

    The line goes after the last item already under "## Contents", or straight
    under the heading when the list is empty.
    """
    lines = index_text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in index_text else "\n"

    start = next((i for i, l in enumerate(lines) if HEADING.match(l.rstrip("\r\n"))), None)
    if start is None:
        return None, ("The front page has no “Contents” heading, so no line was "
                      "added for this chapter. Add a link to it on the front "
                      "page yourself if you want readers to find it there.")
    end = next((i for i in range(start + 1, len(lines))
                if SECTION_END.match(lines[i].rstrip("\r\n"))), len(lines))

    target = link_target(chapter_path)
    for l in lines[start + 1:end]:
        if f"[[{target}|" in l or f"[[{target}]]" in l:
            return None, "The front page already lists this chapter under “Contents”."

    # After the last item and any lines indented under it (its description).
    last = None
    for i in range(start + 1, end):
        text = lines[i].rstrip("\r\n")
        if ITEM.match(text):
            last = i
        elif last is not None and text.startswith((" ", "\t")) and text.strip():
            last = i
    new = line_for(chapter_path, chapter_title_safe(title)) + eol

    if last is not None:
        if not lines[last].endswith(("\n", "\r")):
            lines[last] += eol
        lines.insert(last + 1, new)
    else:
        # An empty list: under the heading, with a blank line either side.
        if not lines[start].endswith(("\n", "\r")):
            lines[start] += eol
        at = start + 1
        block = [new]
        if at < len(lines) and not lines[at].strip():
            at += 1                      # keep the blank line already there
        else:
            block.insert(0, eol)
        if at < len(lines) and lines[at].strip():
            block.append(eol)
        lines[at:at] = block
    return "".join(lines), None


def chapter_title_safe(title):
    return re.sub(r"\s+", " ", title or "").strip() or "Untitled chapter"
