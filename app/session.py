"""
Holds one working session: which chapter, what was found, what will be written.

Two safety rules live here.

  * We read the chapter once and remember exactly what it contained. Just before
    writing we read it again. If it changed in the meantime - because Obsidian
    saved it, or another program touched it - we refuse to write and say so.
  * We only ever write back lines we actually changed. That check is in edits.py
    and it is verified again here before anything is saved.
"""

import os
import re
import time

from . import glossary as glossary_mod
from . import llm, picker, references, terms
from .edits import Edit, apply_edits, line_diff
from .mdmap import DocMap, sha256

VAULT_SKIP_DIRS = {
    ".obsidian", ".git", ".github", ".trash", ".claude", "node_modules",
    "_site", ".vscode", "assets", "backups", "attachments",
}


def is_markdown(path):
    return path.lower().endswith((".md", ".markdown"))


def list_chapters(root):
    """Every markdown file in the vault, nearest the top first."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in VAULT_SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if not is_markdown(fn):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            out.append({
                "path": full,
                "rel": rel,
                "name": fn[:-3],
                "folder": os.path.dirname(rel) or "",
                "size": os.path.getsize(full),
            })
    out.sort(key=lambda c: (c["folder"].casefold(), c["rel"].casefold()))
    return out


def find_vault_root(path):
    """Walk up from a chosen file looking for the top of an Obsidian vault."""
    d = os.path.dirname(os.path.abspath(path))
    probe = d
    for _ in range(6):
        if os.path.isdir(os.path.join(probe, ".obsidian")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return d


def hard_wrapped_paragraphs(docmap):
    """Find paragraphs split over several lines.

    The whole tool assumes one paragraph per line, which is what
    'pandoc --wrap=none' produces. If a chapter is wrapped the other way we say
    so rather than quietly making a mess of it.
    """
    runs, current = [], []
    for i, line in enumerate(docmap.lines):
        plain = (
            docmap.is_prose_line(i)
            and not docmap.in_references(i)
            and not re.match(r"^\s*([-*+]|\d+[.)])\s", line)
            # "<" catches blocks of raw HTML. A chapter that came out of Word
            # carries them wherever markdown had no equivalent - a picture with
            # a caption, a table with merged cells - and each is several lines
            # of markup, not a paragraph somebody wrapped by hand.
            and not line.lstrip().startswith((">", "|", "<", "    "))
        )
        if plain:
            current.append(i)
        else:
            if len(current) > 1:
                runs.append((current[0], current[-1]))
            current = []
    if len(current) > 1:
        runs.append((current[0], current[-1]))
    return runs


class Session:
    def __init__(self, target_path):
        self.target = os.path.abspath(target_path)
        self.mode = "folder" if os.path.isdir(self.target) else "file"
        if self.mode == "folder":
            self.root = self.target
        else:
            self.root = find_vault_root(self.target)

        self.chapter_path = None
        self.docmap = None
        self.read_sha = None
        self.read_mtime = None
        self.findings = []
        self.anchor_edits = {}
        self.notes = []
        self.pages = []
        self.concept_source = ""
        self.options = {}
        self.glossary_path = os.path.join(self.root, "glossary.md")
        self.created = time.time()

    # -- opening --------------------------------------------------------------

    def chapters(self):
        if self.mode == "file":
            return [{
                "path": self.target,
                "rel": os.path.basename(self.target),
                "name": os.path.basename(self.target)[:-3],
                "folder": "",
                "size": os.path.getsize(self.target),
            }]
        return list_chapters(self.root)

    def load_chapter(self, path):
        path = os.path.abspath(path)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        self.chapter_path = path
        self.docmap = DocMap(text, path)
        self.read_sha = sha256(text)
        self.read_mtime = os.path.getmtime(path)
        return self.docmap

    # -- checks before we touch anything --------------------------------------

    def preflight(self, we_just_wrote_it=False):
        """Checks before we touch anything.

        `we_just_wrote_it` is set when the chapter has this moment come out of a
        Word document, through this tool. Without it the freshness check below
        would accuse the tool of the very thing it has just done, which reads as
        a warning that something is wrong when nothing is.
        """
        warnings, blockers = [], []
        path = self.chapter_path

        if not os.access(path, os.W_OK):
            blockers.append(
                "This file is read-only, so nothing could be saved to it. "
                "Check the file's permissions in Finder and try again."
            )

        age = time.time() - self.read_mtime
        if age < 20 and not we_just_wrote_it:
            warnings.append(
                "This chapter was saved by something else a few seconds ago. "
                "If you were just editing it, make sure you have finished before "
                "you continue."
            )

        if picker.obsidian_running():
            warnings.append(
                "Obsidian is open. Please save this chapter in Obsidian "
                "(press Command and S together) and close its tab before you "
                "continue, so your unsaved edits are not lost."
            )

        # Editors that leave a lock or recovery file behind.
        folder = os.path.dirname(path)
        base = os.path.basename(path)
        for candidate in (f".{base}.swp", f".~lock.{base}#", f"~${base}",
                          f".{base}.tmp"):
            if os.path.exists(os.path.join(folder, candidate)):
                warnings.append(
                    "It looks like this chapter is open in another program that "
                    "has not finished with it yet. Close it there first."
                )
                break

        wrapped = hard_wrapped_paragraphs(self.docmap)
        if wrapped:
            warnings.append(
                f"This chapter has {len(wrapped)} paragraph"
                f"{'s' if len(wrapped) != 1 else ''} split across several lines. "
                "The tool still works, and it will not change any line it is not "
                "editing, but your vault normally keeps one paragraph per line."
            )
        return warnings, blockers, bool(wrapped)

    def file_changed_since_read(self):
        """True if the chapter on disk is no longer what we read."""
        try:
            with open(self.chapter_path, "r", encoding="utf-8",
                      errors="replace") as fh:
                return sha256(fh.read()) != self.read_sha
        except OSError:
            return True

    # -- the three analyses ---------------------------------------------------

    def run_analyses(self, options):
        self.options = options
        anchor_style = options.get("anchor_style", "obsidian")
        first_only = options.get("first_mention_only", True)
        chosen = set(options.get("analyses", ["references", "terms", "glossary"]))

        findings, notes = [], []
        self.anchor_edits = {}

        if "references" in chosen:
            f, anchors, n = references.analyse(self.docmap, anchor_style)
            findings += f
            self.anchor_edits = anchors
            notes += n
            if not n and not f:
                notes.append("No citations matching your reference list were "
                             "found in this chapter.")

        self.pages, self.concept_source = terms.discover_concept_pages(
            self.root, self.chapter_path, options.get("concept_folder")
        )

        if "terms" in chosen:
            f, n = terms.analyse(self.docmap, self.pages, first_only)
            findings += f
            notes += n
            if not f and not n:
                notes.append("No mentions of your concept pages were found in "
                             "this chapter.")

        if "glossary" in chosen:
            existing = glossary_mod.existing_glossary_terms(self.glossary_path)
            entries = references.parse_reference_list(self.docmap)
            authors = {s for e in entries for s in e.surnames}
            f, n = glossary_mod.analyse(
                self.docmap,
                existing_terms=existing,
                concept_titles=[p["title"] for p in self.pages],
                author_names=authors,
            )
            notes += n

            if options.get("use_deepseek"):
                extra, reason = llm.suggest_terms(self.docmap.text)
                if extra is None:
                    notes.append(reason)
                else:
                    have = {x["term"].casefold() for x in f}
                    have |= {t.casefold() for t in existing}
                    have |= {p["title"].casefold() for p in self.pages}
                    added = 0
                    for term, definition in extra:
                        if term.casefold() in have:
                            continue
                        have.add(term.casefold())
                        added += 1
                        f.append({
                            "kind": "glossary",
                            "group": f"glossary::{term.casefold()}",
                            "group_label": term,
                            "line": -1, "line_no": 0, "start": 0, "end": 0,
                            "term": term,
                            "definition": definition,
                            "source": "deepseek",
                            "before": "", "match": term, "after": "",
                            "becomes": "",
                            "occurrence": 1, "occurrence_total": 1,
                            "title": f"Glossary entry for “{term}”",
                            "explain": f"Add “{term}” to your glossary. "
                                       "Suggested by DeepSeek after reading the "
                                       "chapter.",
                            "detail_label": "Suggested wording, from DeepSeek",
                            "detail": definition,
                        })
                    notes.append(
                        f"DeepSeek suggested {added} extra term"
                        f"{'s' if added != 1 else ''} on top of the plain checks."
                        if added else
                        "DeepSeek did not find anything the plain checks had missed."
                    )
                    if reason:
                        notes.append(reason)
            if not f and not n:
                notes.append("No glossary terms stood out in this chapter.")
            findings += f

        # Stable ids and a sensible order: through the chapter, top to bottom.
        rank = {"reference": 0, "term": 1, "glossary": 2}
        findings.sort(key=lambda f: (rank[f["kind"]], f["line"], f["start"]))
        for i, f in enumerate(findings):
            f["id"] = f"{f['kind'][0]}{i}"
        self.findings = findings
        self.notes = notes
        return findings, notes

    # -- turning decisions into a finished file --------------------------------

    def _collect_edits(self, accepted_ids, expand_groups):
        by_id = {f["id"]: f for f in self.findings}
        accepted = [by_id[i] for i in accepted_ids if i in by_id]

        edits, used_groups = [], set()
        for f in accepted:
            if f["kind"] == "glossary":
                continue
            edits.append(Edit(f["line"], f["start"], f["end"],
                              f["replacement"], f["id"]))
            used_groups.add(f["group"])

        # "Yes to all" for a term whose later mentions were never offered.
        # These are worked out one concept page at a time, so a mention could in
        # principle land on top of a change the author has already accepted -
        # for instance "Realism" inside "Critical Realism". Anything that
        # overlaps is dropped rather than allowed to collide.
        claimed = {}
        for e in edits:
            claimed.setdefault(e.line, []).append((e.start, e.end))

        if expand_groups:
            first_only = self.options.get("first_mention_only", True)
            for title in expand_groups:
                for extra in terms.expand_to_all_occurrences(
                    self.docmap, self.pages, title, first_only
                ):
                    spans = claimed.setdefault(extra["line"], [])
                    if any(extra["start"] < e and s < extra["end"]
                           for s, e in spans):
                        continue
                    spans.append((extra["start"], extra["end"]))
                    edits.append(Edit(extra["line"], extra["start"], extra["end"],
                                      extra["replacement"], f"all::{title}"))
                used_groups.add(f"term::{title}")

        # A reference entry only gets its anchor if a citation to it was accepted.
        for group in used_groups:
            if group in self.anchor_edits:
                edits.append(self.anchor_edits[group])
        return edits, accepted

    def build_preview(self, accepted_ids, expand_groups):
        edits, accepted = self._collect_edits(accepted_ids, expand_groups)
        new_text, changed = apply_edits(self.docmap, edits)

        # Belt and braces: prove no untouched line moved.
        old_lines = self.docmap.lines
        new_lines = new_text.replace("\r\n", "\n").split("\n")
        if new_lines and new_lines[-1] == "" and self.docmap.had_trailing:
            new_lines = new_lines[:-1]
        assert len(old_lines) == len(new_lines), "line count must never change"
        for i, old in enumerate(old_lines):
            if i not in changed:
                assert old == new_lines[i], f"line {i + 1} changed unexpectedly"

        gloss_terms = [
            (f["term"], f["definition"])
            for f in accepted if f["kind"] == "glossary"
        ]
        chapter_name = os.path.basename(self.chapter_path)
        g_before, g_after, g_added, _, _, _ = glossary_mod.plan_glossary(
            self.glossary_path, gloss_terms, source_name=chapter_name
        )

        return {
            "new_text": new_text,
            "changed_lines": sorted(changed),
            "diff": [
                {"line_no": i + 1, "before": b, "after": a}
                for i, b, a in line_diff(self.docmap, new_text)
            ],
            "glossary_path": self.glossary_path,
            "glossary_exists": os.path.exists(self.glossary_path),
            "glossary_before": g_before,
            "glossary_after": g_after,
            "glossary_added": [r[2] for r in g_added],
            "counts": {
                "references": sum(1 for f in accepted if f["kind"] == "reference"),
                "terms": sum(1 for f in accepted if f["kind"] == "term"),
                "glossary": len(gloss_terms),
                "expanded": len(expand_groups or []),
            },
        }

    def commit(self, accepted_ids, expand_groups):
        if self.file_changed_since_read():
            raise RuntimeError(
                "This chapter changed on disk since the tool read it, so nothing "
                "was saved. Someone or something else has edited it - most likely "
                "Obsidian saving in the background. Close the chapter in Obsidian, "
                "then start again so you are working from the current version."
            )

        preview = self.build_preview(accepted_ids, expand_groups)

        written = []
        if preview["changed_lines"]:
            _atomic_write(self.chapter_path, preview["new_text"])
            written.append(self.chapter_path)

        if preview["glossary_added"]:
            _atomic_write(self.glossary_path, preview["glossary_after"])
            written.append(self.glossary_path)

        # Re-read so a second run in the same session sees the saved version.
        self.load_chapter(self.chapter_path)
        return {
            "written": written,
            "counts": preview["counts"],
            "glossary_added": preview["glossary_added"],
            "changed_lines": len(preview["changed_lines"]),
        }


def _atomic_write(path, text):
    """Write via a temporary file in the same folder, then swap it into place,
    so a crash midway cannot leave a half-written chapter."""
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, f".{os.path.basename(path)}.aa-tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)
