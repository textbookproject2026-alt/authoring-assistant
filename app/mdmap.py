"""
Structural map of one markdown chapter.

The whole tool depends on a single promise: we only ever touch the exact stretch
of characters we are changing. Nothing else in the file moves. This module works
out which stretches are safe to touch and which are off limits (headings, code,
links that already exist, the reference list, and so on).

Lines are kept exactly as they were read. There is no reflowing anywhere.
"""

import hashlib
import re

# --- block level -------------------------------------------------------------

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
ATX_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-{2,})\s*$")
REFS_HEADING_RE = re.compile(
    r"^\s{0,3}(#{1,6})\s*\**\s*"
    r"(references?|bibliography|works\s+cited|reference\s+list|further\s+reading|sources)"
    r"\s*\**\s*:?\s*$",
    re.I,
)

# --- inline level ------------------------------------------------------------
# Anything matching these is already "spoken for" and must never be rewritten.

BARE_REFS_RE = re.compile(
    r"^\s*\**\s*(references?|bibliography|works\s+cited|reference\s+list)"
    r"\s*\**\s*$",
    re.I,
)

INLINE_PROTECTED = [
    re.compile(r"`+[^`\n]*`+"),                      # inline code
    re.compile(r"\[\[[^\[\]\n]*\]\]"),               # existing wikilinks
    re.compile(r"!?\[[^\[\]\n]*\]\([^()\n]*\)"),     # existing markdown links / images
    re.compile(r"!?\[[^\[\]\n]*\]\[[^\[\]\n]*\]"),   # reference-style links
    re.compile(r"\[\^[^\]\n]+\]"),                   # footnote markers
    re.compile(r"<[^<>\n]{1,200}>"),                 # html tags and autolinks
    re.compile(r"\$\$[^\n]*?\$\$"),                  # display maths
    re.compile(r"(?<![\w$])\$[^\s$][^\n$]*?\$(?![\w$])"),  # inline maths
    re.compile(r"https?://\S+"),                     # bare urls
    re.compile(r"\s\^[A-Za-z0-9][A-Za-z0-9-]*\s*$"), # obsidian block ids
]


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_lines(text):
    """Split into lines while remembering exactly how the file ended.

    Returns (lines, newline, had_trailing_newline) so the file can be put back
    together byte for byte apart from the lines we deliberately change.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    body = text.replace("\r\n", "\n")
    had_trailing = body.endswith("\n")
    if had_trailing:
        body = body[:-1]
    return body.split("\n"), newline, had_trailing


def join_lines(lines, newline, had_trailing):
    out = newline.join(lines)
    if had_trailing:
        out += newline
    return out


def merge_spans(spans):
    """Collapse overlapping (start, end) pairs into a tidy sorted list."""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(s) for s in merged]


class DocMap:
    """Everything we need to know about the shape of one chapter."""

    def __init__(self, text, path=None):
        self.path = path
        self.text = text
        self.sha = sha256(text)
        self.lines, self.newline, self.had_trailing = split_lines(text)

        n = len(self.lines)
        self.frontmatter = set()
        self.code = set()
        self.heading = set()
        self.protected_spans = [[] for _ in range(n)]

        self.heading_like = set()

        self._scan_frontmatter()
        self._scan_blocks()
        self._scan_heading_like()
        self.refs_start, self.refs_end = self._find_references_section()
        self._scan_inline()

    # -- block scanning -------------------------------------------------------

    def _scan_frontmatter(self):
        if not self.lines or self.lines[0].strip() != "---":
            return
        for i in range(1, len(self.lines)):
            self.frontmatter.add(i)
            if self.lines[i].strip() in ("---", "..."):
                break
        self.frontmatter.add(0)

    def _scan_blocks(self):
        in_fence = False
        fence_marker = None
        for i, line in enumerate(self.lines):
            if i in self.frontmatter:
                continue
            m = FENCE_RE.match(line)
            if m:
                marker = m.group(1)[0]
                if not in_fence:
                    in_fence, fence_marker = True, marker
                    self.code.add(i)
                    continue
                if marker == fence_marker:
                    in_fence, fence_marker = False, None
                    self.code.add(i)
                    continue
            if in_fence:
                self.code.add(i)
                continue
            if ATX_HEADING_RE.match(line):
                self.heading.add(i)
            elif (
                SETEXT_RE.match(line)
                and i > 0
                and self.lines[i - 1].strip()
                and (i - 1) not in self.code
                and (i - 1) not in self.frontmatter
            ):
                # A setext heading: the underline and the text above it.
                self.heading.add(i)
                self.heading.add(i - 1)
            elif line.startswith("    ") and self._is_indented_code(i):
                self.code.add(i)

    def _is_indented_code(self, i):
        """A four-space indent is only code if it is not a continuation of a list."""
        j = i - 1
        while j >= 0:
            prev = self.lines[j]
            if not prev.strip():
                j -= 1
                continue
            if re.match(r"^\s*([-*+]|\d+[.)])\s", prev):
                return False
            return not prev.startswith("    ")
        return True

    def _scan_heading_like(self):
        """Find lines that act as headings without being marked up as one.

        Plenty of chapters carry subheadings such as "II.i From Dualism to
        Monism" written as ordinary lines. Markdown sees a paragraph; the author
        sees a heading. We treat them as headings so nothing is inserted into
        them. A line qualifies only if it stands alone between blank lines, is
        short, and has no sentence-ending punctuation - which a real paragraph
        would almost always have.
        """
        n = len(self.lines)
        for i, line in enumerate(self.lines):
            if i in self.code or i in self.frontmatter or i in self.heading:
                continue
            s = line.strip()
            if not s or len(s) > 100:
                continue
            if re.match(r"^([-+>|]|\*\s|\d+[.)]\s)", s):
                continue
            if s[-1] in ".,;:!?":
                continue
            prev_blank = i == 0 or not self.lines[i - 1].strip() \
                or (i - 1) in self.heading
            next_blank = i == n - 1 or not self.lines[i + 1].strip()
            if not (prev_blank and next_blank):
                continue
            if not re.match(r"^(\*\*)?[A-Z0-9IVXLC“\"]", s):
                continue
            self.heading.add(i)
            self.heading_like.add(i)

    def _find_references_section(self):
        """Locate the reference list: its heading through to the next heading of
        the same or higher level (or the end of the file)."""
        for i, line in enumerate(self.lines):
            if i in self.code or i in self.frontmatter:
                continue
            m = REFS_HEADING_RE.match(line)
            if not m:
                if i in self.heading_like and BARE_REFS_RE.match(line):
                    return i, len(self.lines)
                continue
            level = len(m.group(1))
            end = len(self.lines)
            for j in range(i + 1, len(self.lines)):
                if j in self.code:
                    continue
                hm = ATX_HEADING_RE.match(self.lines[j])
                if hm:
                    depth = len(self.lines[j].strip().split(" ")[0])
                    if depth <= level:
                        end = j
                        break
            return i, end
        return None, None

    # -- inline scanning ------------------------------------------------------

    def _scan_inline(self):
        for i, line in enumerate(self.lines):
            spans = []
            for pattern in INLINE_PROTECTED:
                for m in pattern.finditer(line):
                    spans.append((m.start(), m.end()))
            self.protected_spans[i] = merge_spans(spans)

    # -- questions the analyses ask -------------------------------------------

    def in_references(self, i):
        return self.refs_start is not None and self.refs_start <= i < self.refs_end

    def is_prose_line(self, i):
        """True for lines whose body text we are willing to rewrite."""
        return not (
            i in self.code
            or i in self.frontmatter
            or i in self.heading
            or not self.lines[i].strip()
        )

    def is_free(self, i, start, end):
        """True if this exact stretch of characters is safe to rewrite."""
        for pstart, pend in self.protected_spans[i]:
            if start < pend and pstart < end:
                return False
        return True

    def reference_entry_lines(self):
        """Line numbers of the individual entries in the reference list."""
        if self.refs_start is None:
            return []
        out = []
        for i in range(self.refs_start + 1, self.refs_end):
            if i in self.code or not self.lines[i].strip():
                continue
            if ATX_HEADING_RE.match(self.lines[i]):
                continue
            out.append(i)
        return out


# --- reading a sentence out of a line ----------------------------------------

_SENT_END = re.compile(r"[.!?][\"')\]]?\s")


def sentence_around(line, start, end, budget=340):
    """Pull the sentence containing (start, end) out of a long paragraph line,
    so the author is shown a sentence rather than a wall of text."""
    left = 0
    for m in _SENT_END.finditer(line, 0, start):
        # Don't break on abbreviations or initials such as "et al." or "N. K."
        head = line[max(0, m.start() - 6):m.start() + 1]
        if re.search(r"(al\.|[A-Z]\.|Eds?\.|pp?\.|vs\.|cf\.|e\.g\.|i\.e\.)$", head):
            continue
        left = m.end()
    right = len(line)
    m = _SENT_END.search(line, end)
    while m:
        head = line[max(0, m.start() - 6):m.start() + 1]
        if re.search(r"(al\.|[A-Z]\.|Eds?\.|pp?\.|vs\.|cf\.|e\.g\.|i\.e\.)$", head):
            m = _SENT_END.search(line, m.end())
            continue
        right = m.end()
        break

    before = line[left:start]
    match = line[start:end]
    after = line[end:right]

    if budget is None:
        return "", before + match + after, ""

    # Keep the excerpt readable if the sentence is still enormous.
    room = max(60, (budget - len(match)) // 2)
    prefix = suffix = ""
    if len(before) > room:
        before = before[-room:]
        prefix = "…"
    if len(after) > room:
        after = after[:room]
        suffix = "…"
    return prefix + before.lstrip(), match, after.rstrip() + suffix
