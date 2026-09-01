"""
Applying changes to a chapter.

One rule, enforced here rather than trusted to good intentions: an edit is a
replacement of a specific stretch of characters on a specific line. Lines with no
accepted edit are copied through untouched, byte for byte. Nothing is reflowed,
re-wrapped, or reformatted. This is what keeps the vault's line-by-line history
readable after the tool has run.
"""

from .mdmap import join_lines


class Edit:
    def __init__(self, line, start, end, replacement, origin=""):
        self.line = line
        self.start = start
        self.end = end
        self.replacement = replacement
        self.origin = origin

    def __repr__(self):
        return f"Edit(line={self.line}, {self.start}:{self.end} -> {self.replacement!r})"


class ConflictingEdits(Exception):
    pass


def apply_edits(docmap, edits):
    """Return the new file text plus the set of line numbers that changed.

    Edits are applied right to left within each line so earlier offsets stay valid.
    """
    lines = list(docmap.lines)
    by_line = {}
    for e in edits:
        by_line.setdefault(e.line, []).append(e)

    changed = set()
    for lineno, group in by_line.items():
        group.sort(key=lambda e: (e.start, e.end))
        # Two accepted changes must never overlap on the same line.
        for a, b in zip(group, group[1:]):
            if b.start < a.end:
                raise ConflictingEdits(
                    f"Two changes overlap on line {lineno + 1}: "
                    f"{a.origin!r} and {b.origin!r}"
                )
        original = lines[lineno]
        text = original
        for e in reversed(group):
            text = text[:e.start] + e.replacement + text[e.end:]
        if text != original:
            lines[lineno] = text
            changed.add(lineno)

    return join_lines(lines, docmap.newline, docmap.had_trailing), changed


def line_diff(docmap, new_text):
    """Pairs of (line number, before, after) for every line that changed.

    Used only for showing the author what will happen; nothing depends on it.
    """
    from .mdmap import split_lines

    new_lines, _, _ = split_lines(new_text)
    out = []
    for i, old in enumerate(docmap.lines):
        new = new_lines[i] if i < len(new_lines) else ""
        if old != new:
            out.append((i, old, new))
    return out
