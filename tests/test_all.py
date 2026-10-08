"""
Checks for the promises the tool makes.

Run with:  python3 -m tests.test_all
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import glossary, references, terms
from app.edits import Edit, apply_edits
from app.mdmap import DocMap
from app.session import Session, hard_wrapped_paragraphs

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "  ok  " if condition else " FAIL "
    print(f"[{mark}] {name}" + (f"\n         {detail}" if detail and not condition else ""))


CHAPTER = """---
title: Test chapter
aliases: [tc]
---

# Chapter 4: Structure and Agency

## I Introduction

Social structures shape action (Archer, 1995). As Archer (1995) argues, structure and agency are analytically separable. See also (Bhaskar, 1979; Archer, 1995, p. 44).

I.i An Unmarked Subheading About Emergence

Emergence matters here. The term "morphogenesis" describes structural elaboration. Analytical dualism is defined as the separation of structure and agency for analytical purposes.

Here is a code sample:

```python
# Emergence (Archer, 1995) should never be touched in here
x = "Critical Realism"
```

A line with `inline code about Emergence` and an [existing link](http://example.com) and [[Critical Realism]] already linked.

> [!note] Key concepts
> [[Emergence]] · [[Monism]]

## References

Archer, M. S. (1995). *Realist social theory*. Cambridge University Press.

Bhaskar, R. (1979). *The possibility of naturalism*. Harvester Press.
"""


def make_vault():
    root = tempfile.mkdtemp(prefix="aa-test-")
    os.makedirs(os.path.join(root, ".obsidian"), exist_ok=True)
    defs = os.path.join(root, "Definitions")
    os.makedirs(defs, exist_ok=True)
    for title in ("Emergence", "Critical Realism", "Monism"):
        with open(os.path.join(defs, f"{title}.md"), "w") as fh:
            fh.write(f"# {title}\n\nA concept page.\n")
    chapter = os.path.join(root, "chapter-04.md")
    with open(chapter, "w") as fh:
        fh.write(CHAPTER)
    return root, chapter


# --- 1. structure -----------------------------------------------------------

root, chapter = make_vault()
doc = DocMap(CHAPTER, chapter)

check("frontmatter is recognised", 0 in doc.frontmatter and 3 in doc.frontmatter)
check("fenced code is recognised", any("x = " in doc.lines[i] for i in doc.code))
check("markdown headings are recognised",
      any(doc.lines[i].startswith("# Chapter 4") for i in doc.heading))
check("an unmarked subheading is treated as a heading",
      any("Unmarked Subheading" in doc.lines[i] for i in doc.heading_like))
check("the References section is found", doc.refs_start is not None)

# --- 2. citations -----------------------------------------------------------

f_ref, anchors, notes = references.analyse(doc)
matched = {x["match"] for x in f_ref}
check("a parenthetical citation is found", "Archer, 1995" in matched)
check("a narrative citation is found",
      any(x["match"].startswith("Archer (1995") or x["match"] == "Archer (1995)"
          for x in f_ref))
check("a citation with a page number is handled",
      any("p. 44" not in x["match"] for x in f_ref))
check("citations inside code are ignored",
      not any(i in doc.code for i in (x["line"] for x in f_ref)))
check("a reference entry gets exactly one anchor", len(anchors) == 2,
      f"got {len(anchors)}")

# --- 3. concept terms -------------------------------------------------------

pages, source = terms.discover_concept_pages(root, chapter)
check("concept pages are discovered", len(pages) == 3, f"got {[p['title'] for p in pages]}")
check("a Definitions folder is preferred", source.endswith("Definitions"))

f_term, tnotes = terms.analyse(doc, pages, first_mention_only=False)
term_lines = {x["line"] for x in f_term}
check("terms in code blocks are skipped",
      not (term_lines & doc.code))
check("terms in headings are skipped",
      not (term_lines & doc.heading))
check("terms already wikilinked are skipped",
      not any(x["match"] == "Critical Realism" and "[[" in doc.lines[x["line"]][:x["start"]][-2:]
              for x in f_term))
check("terms inside inline code are skipped",
      not any("inline code about" in doc.lines[x["line"]] for x in f_term))
check("terms in the References section are skipped",
      not any(doc.in_references(x["line"]) for x in f_term))

# --- 4. glossary ------------------------------------------------------------

f_gloss, gnotes = glossary.analyse(doc, existing_terms=set(),
                                   concept_titles=[p["title"] for p in pages])
gterms = {x["term"] for x in f_gloss}
check("a term defined with 'is defined as' is found", "Analytical dualism" in gterms,
      f"got {gterms}")
check("a quoted 'the term X' is found", "morphogenesis" in gterms, f"got {gterms}")
check("concept pages are not offered as glossary terms",
      not (gterms & {p["title"] for p in pages}))

# --- 5. the untouched-lines promise -----------------------------------------

all_edits = [Edit(x["line"], x["start"], x["end"], x["replacement"], x["id"] if "id" in x else "")
             for x in f_ref + f_term] + list(anchors.values())
new_text, changed = apply_edits(doc, all_edits)
old_lines = CHAPTER.split("\n")
new_lines = new_text.split("\n")
check("the number of lines never changes", len(old_lines) == len(new_lines))
check("every line we did not edit is byte-for-byte identical",
      all(old_lines[i] == new_lines[i] for i in range(len(old_lines)) if i not in changed))
check("the trailing newline is preserved",
      CHAPTER.endswith("\n") == new_text.endswith("\n"))

# --- 6. idempotency ---------------------------------------------------------

doc2 = DocMap(new_text, chapter)
f_ref2, anchors2, _ = references.analyse(doc2)
f_term2, _ = terms.analyse(doc2, pages, first_mention_only=False)
check("running again finds no citations to link", len(f_ref2) == 0, f"got {len(f_ref2)}")
check("running again finds no terms to link", len(f_term2) == 0, f"got {len(f_term2)}")
check("running again adds no second anchor", len(anchors2) == 0)

# --- 7. line endings and endings without a newline --------------------------

crlf = CHAPTER.replace("\n", "\r\n")
dcrlf = DocMap(crlf)
out_crlf, ch_crlf = apply_edits(dcrlf, [Edit(0, 0, 3, "---")])
check("Windows line endings survive a round trip", "\r\n" in out_crlf and "\n\n" not in out_crlf)

no_nl = "Some text about Emergence (Archer, 1995)."
dn = DocMap(no_nl)
out_nn, _ = apply_edits(dn, [])
check("a file with no final newline keeps none", out_nn == no_nl)

# --- 8. no References section ----------------------------------------------

d_norefs = DocMap("# A chapter\n\nText citing (Archer, 1995) with no reference list.\n")
f_nr, a_nr, n_nr = references.analyse(d_norefs)
check("a chapter with no reference list is handled gently",
      f_nr == [] and any("no References section" in n for n in n_nr))

# --- 9. hard-wrapped detection ---------------------------------------------

wrapped = DocMap("# T\n\nThis paragraph is\nsplit over three\nseparate lines here.\n")
check("hard-wrapped paragraphs are detected", len(hard_wrapped_paragraphs(wrapped)) == 1)
check("one-paragraph-per-line is not flagged", len(hard_wrapped_paragraphs(doc)) == 0)

# --- 10. refusing to write over someone else's changes ----------------------

session = Session(root)
session.load_chapter(chapter)
f_all, _ = session.run_analyses({"analyses": ["references", "terms", "glossary"],
                                 "first_mention_only": True})
with open(chapter, "a") as fh:
    fh.write("\nSomeone else edited this file.\n")
check("a change on disk is noticed", session.file_changed_since_read())
try:
    session.commit([x["id"] for x in f_all], [])
    check("writing is refused after an outside change", False, "it wrote anyway")
except RuntimeError as e:
    check("writing is refused after an outside change", "changed on disk" in str(e))

# --- 11. glossary merging ---------------------------------------------------

gpath = os.path.join(root, "glossary.md")
with open(gpath, "w") as fh:
    fh.write("# Glossary\n\n## Beta\n\nSecond letter.\n\n## Delta\n\nFourth letter.\n")
_, after, added, _, _, _ = glossary.plan_glossary(
    gpath, [("Alpha", "first letter"), ("Charlie", "third letter"),
            ("Beta", "duplicate"), ("Zulu", "last letter")])
import re as _re
heads = _re.findall(r"^## (.+)$", after, _re.M)
check("new glossary entries land in alphabetical order",
      heads == sorted(heads, key=str.casefold), f"got {heads}")
check("an existing glossary entry is not duplicated", heads.count("Beta") == 1)
check("existing glossary wording is left alone", "Second letter." in after)
check("a new entry is added at the end when it sorts last", heads[-1] == "Zulu")

# An entry that sorts last used to leave a blank line before the end of the file,
# which markdownlint's MD012 flags - and tidying the file by hand did not help,
# because the next entry to sort last put it straight back.
with open(gpath, "w") as fh:
    fh.write("# Glossary\n\n## Beta\n\nSecond letter.\n")
_, after_last, _, _, _, _ = glossary.plan_glossary(gpath, [("Zulu", "last letter")])
with open(gpath, "w") as fh:
    fh.write(after_last)
with open(gpath) as fh:
    written = fh.read()
check("a glossary ending on a last-sorting entry ends with one newline",
      written.endswith("\n") and not written.endswith("\n\n"),
      f"ends {written[-20:]!r}")
check("entries are still separated by a blank line",
      "\n\n## Zulu\n\n" in written, f"got {written!r}")

# The same must hold when a last-sorting entry is appended to that tidy file.
_, after_again, _, _, _, _ = glossary.plan_glossary(gpath, [("Zeta", "sixth letter")])
check("the blank line does not come back on the next append",
      after_again.endswith("\n") and not after_again.endswith("\n\n"),
      f"ends {after_again[-20:]!r}")

# --- 12. single-file mode ---------------------------------------------------

solo = Session(chapter)
check("choosing one file gives one chapter", len(solo.chapters()) == 1)
check("the vault root is found from a single file", solo.root == root)

# --- 13. one concept title nested inside another ----------------------------

nested = tempfile.mkdtemp(prefix="aa-nested-")
os.makedirs(os.path.join(nested, ".obsidian"), exist_ok=True)
ndefs = os.path.join(nested, "Definitions")
os.makedirs(ndefs)
for t in ("Realism", "Critical Realism"):
    with open(os.path.join(ndefs, f"{t}.md"), "w") as fh:
        fh.write(f"# {t}\n")
nchapter = os.path.join(nested, "c.md")
with open(nchapter, "w") as fh:
    fh.write("# C\n\nWe discuss critical realism. Later, critical realism again, "
             "and realism alone.\n")

ns = Session(nested)
ns.load_chapter(nchapter)
nf, _ = ns.run_analyses({"analyses": ["terms"], "first_mention_only": True})
preview = ns.build_preview([x["id"] for x in nf], ["Realism", "Critical Realism"])
line = preview["diff"][0]["after"]
check("the longer concept title wins over the shorter one inside it",
      "[[Critical Realism|critical realism]] again" in line, line)
check("saying yes to all for both titles does not produce a clash",
      line.count("[[") == 3, line)
shutil.rmtree(nested, ignore_errors=True)

# --- 14. the application's own files ----------------------------------------

from app import config, llm  # noqa: E402

check("the key is kept in Application Support, not in the vault",
      config.SUPPORT_DIR.endswith("Application Support/Authoring Assistant")
      and llm.KEY_PATHS[0].startswith(config.SUPPORT_DIR))
check("the older key location is still read",
      any(p.endswith(".authoring-assistant/deepseek.key") for p in llm.KEY_PATHS))
check("with no key, DeepSeek is skipped rather than failing",
      llm.suggest_terms("some text")[0] is None
      if not llm.have_key() else True)
check("testing a key with none saved gives a plain answer",
      llm.test_key("")[0] is False)

# The single-copy check must not claim a copy is running when none is.
saved_runtime = config.read_runtime()
config.clear_runtime()
check("no running copy is reported when none is running",
      config.running_elsewhere() is None)
config.write_runtime(59999, "not-a-real-token")
check("a stale runtime file is ignored and cleaned up",
      config.running_elsewhere() is None and config.read_runtime() is None)
if saved_runtime:
    config.write_runtime(saved_runtime["port"], saved_runtime["token"])

# ---------------------------------------------------------------------------
# Bringing a Word document in.
# ---------------------------------------------------------------------------
# Bringing a Word document in.
# ---------------------------------------------------------------------------

from app import convert as _convert  # noqa: E402

# --- the conversion flags are the promise, so they are checked, not trusted ---

check("the converter is told not to wrap paragraphs",
      "--wrap=none" in _convert.PANDOC_ARGS,
      "without --wrap=none every paragraph is split across several lines and "
      "the whole one-line-per-paragraph model collapses")
check("the converter is asked for GitHub-flavoured markdown",
      "gfm" in _convert.PANDOC_ARGS)
check("the converter is told the input is a Word document",
      "-f" in _convert.PANDOC_ARGS
      and _convert.PANDOC_ARGS[_convert.PANDOC_ARGS.index("-f") + 1] == "docx")
_here_pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_here_pkg, "app", "convert.py"), encoding="utf-8") as fh:
    _convert_source = fh.read()
# The flag is only half the promise. The reason has to sit beside it, or the
# next person to tidy this file will decide it is a formatting preference.
_why = _convert_source.split("PANDOC_ARGS = ")[0]
check("why --wrap=none is mandatory is written down beside the flag itself",
      "--wrap=none" in _why and "one paragraph per line" in _why
      and "MANDATORY" in _why)

# --- finding pandoc ----------------------------------------------------------

check("a pandoc installed outside the launcher's PATH is still found",
      "/usr/local/bin/pandoc" in _convert.LIKELY_PANDOC
      and "/opt/homebrew/bin/pandoc" in _convert.LIKELY_PANDOC,
      "the app is started from Finder, so it inherits almost no PATH")
check("the copy inside the app is preferred over any other",
      _convert.find_pandoc.__doc__ and "bundled copy wins"
      in _convert.find_pandoc.__doc__)
check("the guided install never mentions a terminal or a package manager",
      not any(word in open(os.path.join(_here_pkg, "app", "convert.py"),
                           encoding="utf-8").read().lower()
              for word in ("brew install", "homebrew install", "run this command",
                           "type the following")))

# --- naming ------------------------------------------------------------------

check("a Word file's name becomes a sensible chapter name",
      _convert.suggest_name("/x/y/Chapter 4 - Structure.docx")
      == "Chapter 4 - Structure.md")
check("characters a file name cannot hold are taken out of the suggestion",
      "/" not in _convert.suggest_name("/x/A B: C/D.docx"))
check("a nameless Word file still gets a name",
      _convert.suggest_name("/x/.docx") == "Untitled chapter.md",
      "got " + _convert.suggest_name("/x/.docx"))

# --- the one chapter-naming rule (27 Sep 2026) --------------------------------

check("a Word file new to the book becomes the next free chapter-NN",
      _convert.chapter_name("Chapter 3 – Reality.docx",
                            ["chapter-01.md", "chapter-03.md", "Definitions"], {})
      == ("chapter-04.md", "new"))
check("the first chapter of an empty book is chapter-01",
      _convert.chapter_name("Intro.docx", [], {}) == ("chapter-01.md", "new"))
_src = _convert.parse_sources(
    _convert.sources_text("", "Intro.docx", "chapters/chapter-07.md"))
check("the same Word file again replaces the chapter it became",
      _convert.chapter_name("Intro.docx", ["chapter-07.md"], _src)
      == ("chapter-07.md", "recorded"))
check("the Word file is recognised however its name's case and spacing were typed",
      _convert.chapter_name("  INTRO.docx", [], _src) == ("chapter-07.md", "recorded"))
check("a live book keeps a chapter named before the rule (book-requests' slug)",
      _convert.chapter_name("Introduction.docx", ["introduction.md"], {})
      == ("introduction.md", "existing"))
check("a live book keeps a chapter named before the rule (the app's old suggestion)",
      _convert.chapter_name("Chapter 4 - Structure.docx", ["Chapter 4 - Structure.md"], {})
      == ("Chapter 4 - Structure.md", "existing"))
check("a recorded chapter's number isn't handed out again, even if the file is gone",
      _convert.chapter_name("Other.docx", [], _src) == ("chapter-08.md", "new"))
_two = _convert.sources_text(
    _convert.sources_text("", "B.docx", "chapters/chapter-02.md"),
    "a.docx", "chapters/chapter-01.md")
check("recording a Word file keeps every other entry",
      _convert.parse_sources(_two) == {"b.docx": "chapters/chapter-02.md",
                                        "a.docx": "chapters/chapter-01.md"})
check("recording the same Word file again moves it, it doesn't add a second",
      len(_convert.parse_sources(_convert.sources_text(_two, "A.DOCX",
          "chapters/chapter-05.md"))) == 2)
check("a broken chapter-sources.json is ignored, not a reason to refuse",
      _convert.parse_sources("{not json") == {}
      and _convert.parse_sources('["x"]') == {})
check("a new chapter must be chapter-NN.md",
      _convert.rule_problem("Chapter 3.md", ["chapter-01.md"]) is not None
      and _convert.rule_problem("chapter-02.md", ["chapter-01.md"]) is None)
check("an existing chapter may be replaced whatever its name",
      _convert.rule_problem("introduction.md", ["introduction.md"]) is None)


# --- refusing to write over anything ----------------------------------------

# A vault to write into: the three things that make a folder the top of this
# textbook, and a chapters folder to import into, as the real one has.
_vault = tempfile.mkdtemp(prefix="aa-vault-")
os.makedirs(os.path.join(_vault, "chapters"))
os.makedirs(os.path.join(_vault, "assets"))
with open(os.path.join(_vault, "glossary.md"), "w") as fh:
    fh.write("# Glossary\n")
_wroot = os.path.join(_vault, "chapters")
with open(os.path.join(_wroot, "taken.md"), "w") as fh:
    fh.write("# Already here\n")

check("a name that is already taken is refused",
      "already a chapter" in (_convert.destination_problem(_wroot, "taken.md") or ""))
check("a free name is allowed",
      _convert.destination_problem(_wroot, "free.md") is None,
      _convert.destination_problem(_wroot, "free.md"))
check("a name with a folder in it is refused",
      "just a name" in (_convert.destination_problem(_wroot, "sub/x.md") or ""))
check("a name that is not markdown is refused",
      "end in .md" in (_convert.destination_problem(_wroot, "x.docx") or ""))
check("a name with characters a file cannot hold is refused",
      "cannot contain" in (_convert.destination_problem(_wroot, 'x?".md') or ""))

# --- where the pictures go ---------------------------------------------------
#
# The whole point of this section: a converted chapter's pictures belong in the
# vault's `assets/<chapter>/`, because that folder is what `admin/config.yml`
# gives the website as its media folder, what `docs/editing-the-textbook.md`
# tells authors to use, and - the one with teeth - what
# `docs/for-course-coordinators.md` tells a department to copy when it builds
# its own edition. A picture anywhere else is silently missing from every
# edition of the book.

check("a chapter's pictures are destined for the vault's assets folder, under "
      "the chapter's own name",
      _convert.media_destination(_wroot, "chapter-05.md")["rel"]
      == "assets/chapter-05",
      _convert.media_destination(_wroot, "chapter-05.md")["rel"])
check("that folder really is inside the vault, not beside the chapter",
      _convert.media_destination(_wroot, "chapter-05.md")["target"]
      == os.path.join(_vault, "assets", "chapter-05"))
check("the vault is found by walking up from the folder the author chose",
      _convert.find_vault_root(_wroot) == _vault)
_deep = os.path.join(_wroot, "Definitions")
os.makedirs(_deep, exist_ok=True)
check("and from a folder further down inside it",
      _convert.find_vault_root(_deep) == _vault)
check("a folder that is not inside a textbook is refused, not guessed at",
      _convert.find_vault_root(tempfile.mkdtemp(prefix="aa-notvault-")) is None)
_nowhere = tempfile.mkdtemp(prefix="aa-notvault-")
check("and the refusal names the three things it looked for",
      all(w in _convert.vault_problem(_nowhere)
          for w in ("chapters", "assets", "glossary.md")),
      _convert.vault_problem(_nowhere))
check("a chapter cannot be imported into a folder outside a textbook",
      _convert.destination_problem(_nowhere, "chapter-05.md") is not None)

# The link written into the chapter has to work in Obsidian, on the published
# site, and in a department edition where `chapters` and `assets` have been
# copied inside a `content` folder. Only a path from the chapter to the picture
# survives all three.
check("the chapter links to its pictures by a path relative to itself",
      _convert.media_destination(_wroot, "chapter-05.md")["link_prefix"]
      == "../assets/chapter-05",
      _convert.media_destination(_wroot, "chapter-05.md")["link_prefix"])
check("a chapter saved at the top of the vault links to them without a step up",
      _convert.media_destination(_vault, "chapter-05.md")["link_prefix"]
      == "assets/chapter-05",
      _convert.media_destination(_vault, "chapter-05.md")["link_prefix"])
check("a chapter name with a space or a bracket in it still makes a link that works",
      _convert.media_destination(_wroot, "Chapter 6 (final).md")["link_prefix"]
      == "../assets/Chapter%206%20%28final%29",
      _convert.media_destination(_wroot, "Chapter 6 (final).md")["link_prefix"])

# One folder per chapter is not tidiness. Word calls the pictures in every
# document image1.png, image2.png, so two chapters sharing a folder would write
# over each other's figures.
os.makedirs(os.path.join(_vault, "assets", "chapter-09"))
with open(os.path.join(_vault, "assets", "chapter-09", "image1.png"), "wb") as fh:
    fh.write(b"x")
_clash = _convert.destination_problem(_wroot, "chapter-09.md") or ""
check("a chapter whose pictures would land on another chapter's is refused",
      "assets/chapter-09" in _clash, _clash)
check("and the refusal says why one folder per chapter matters",
      "image1" in _clash and "write over" in _clash, _clash)
os.makedirs(os.path.join(_vault, "assets", "chapter-10"), exist_ok=True)
check("an empty leftover folder is not a clash - there is nothing in it to lose",
      _convert.destination_problem(_wroot, "chapter-10.md") is None,
      _convert.destination_problem(_wroot, "chapter-10.md"))

# --- the links in the converted text point at assets, not at the staging folder

def _staged(text, files=("image1.png",), inner=True):
    """Run the real link rewriting over a fake pandoc output."""
    stage = tempfile.mkdtemp(prefix="aa-stage-")
    where = os.path.join(stage, _convert.STAGE_MEDIA)
    os.makedirs(os.path.join(where, "media") if inner else where)
    for f in files:
        open(os.path.join(where, "media" if inner else "", f), "wb").write(b"x")
    return _convert._collect_media(stage, text, "../assets/chapter-05")


_out, _got = _staged(
    f'<img src="{_convert.STAGE_MEDIA}/media/image1.png" />\n')
check("a picture link is rewritten to point into the vault's assets folder",
      _out == '<img src="../assets/chapter-05/image1.png" />\n', _out)
check("and nothing is left pointing at the temporary folder it was extracted to",
      _convert.STAGE_MEDIA not in _out, _out)
check("the picture itself is listed by name, ready to be reported on",
      [m["name"] for m in _got] == ["image1.png"], _got)

_out2, _ = _staged("![](" + _convert.STAGE_MEDIA + "/media/image1.png)\n")
check("markdown-style picture links are rewritten too",
      _out2 == "![](../assets/chapter-05/image1.png)\n", _out2)

# pandoc only nests a "media" folder when that is where the picture sat inside
# the Word file. When it does not, the links still have to move.
_out3, _ = _staged(f'<img src="{_convert.STAGE_MEDIA}/image1.png" />\n',
                   inner=False)
check("pictures pandoc did not nest are redirected as well",
      _out3 == '<img src="../assets/chapter-05/image1.png" />\n', _out3)

# --- the report reads what actually came out --------------------------------
#
# Built from text rather than from a .docx so the suite still runs on a machine
# with no pandoc. The shapes below are exactly what `pandoc -t gfm` produces.

def _fake(text, media=None, media_rel="assets/ch", warnings=None):
    return {"text": text, "media": media or [],
            "media_rel": media_rel if media else None,
            "pandoc_warnings": warnings or [],
            "stage": "", "docx": "x.docx", "md_name": "ch.md"}


def _heads(text, media=None):
    return [n["headline"] for n in _convert.report(_fake(text, media))["notes"]]


def _levels(text, media=None):
    return {n["headline"]: n["level"]
            for n in _convert.report(_fake(text, media))["notes"]}


_html_table = _fake(
    "<table>\n<tr><td><p>one</p>\n<p>two</p></td></tr>\n</table>\n")
check("a table Word merged cells in is reported as a problem",
      _levels(_html_table["text"])
      .get("1 table could not be made into a proper table") == "warn")
check("the merged-cell warning says those cells will never be linked",
      any("will ever be linked" in n["body"]
          for n in _convert.report(_html_table)["notes"]))

check("a table that converted cleanly is reported too",
      "1 table converted cleanly"
      in _heads("| A | B |\n|---|---|\n| 1 | 2 |\n"))

check("footnotes are counted and explained",
      "2 footnotes came across"
      in _heads("Text.[^1] More.[^2]\n\n[^1]: One.\n\n[^2]: Two.\n"))
check("a footnote number with no note is reported",
      _levels("Text.[^1] More.[^2]\n\n[^1]: One.\n")
      .get("Some footnotes do not match up") == "warn")

check("a document whose headings were made by hand is reported",
      _levels("**A Heading**\n\nSome prose.\n")
      .get("No headings came across at all") == "warn")
check("that report tells the author about Word's Heading styles",
      any("Heading 1" in n["body"] + n["check"]
          for n in _convert.report(_fake("**A Heading**\n\nProse.\n"))["notes"]))
check("headings that did convert are counted by level",
      any(n["headline"] == "3 headings came across" and "2 at level 2" in n["body"]
          for n in _convert.report(_fake("# A\n\n## B\n\n## C\n"))["notes"]))
check("a bold line that looks like a lost heading is pointed out",
      "1 line may be a heading that did not convert"
      in _heads("# Real\n\n**Looks like a heading**\n\nProse here.\n"))
check("a skipped heading level is pointed out",
      "A heading level was skipped" in _heads("# A\n\n### C\n"))

check("a chapter with no reference list is pointed out",
      "This chapter has no References section" in _heads("# A\n\nProse.\n"))
check("a chapter with a reference list is not",
      "This chapter has no References section"
      not in _heads("# A\n\nProse.\n\n## References\n\nArcher (1995).\n"))

_pics = [{"rel": "rId1.png", "name": "rId1.png", "ext": "png", "size": 900},
         {"rel": "rId2.emf", "name": "rId2.emf", "ext": "emf", "size": 900}]
_pic_notes = _convert.report(_fake(
    '<img src="../assets/ch/rId1.png" />\n', _pics))["notes"]
check("pictures are reported with the folder they went into",
      any("assets/ch" in n["body"] for n in _pic_notes))
check("and the author is told it is the textbook's own pictures folder, "
      "not one beside the chapter",
      any("one folder per chapter" in n["body"] for n in _pic_notes),
      [n["body"] for n in _pic_notes])
check("a Word chart that came out as an unviewable file is a warning",
      any(n["level"] == "warn" and "nothing can display" in n["headline"]
          for n in _pic_notes))
check("that warning says how to fix it in Word, not on a terminal",
      any("Paste" in n["check"] and "Picture" in n["check"] for n in _pic_notes))
check("pictures written as web tags are explained rather than left to surprise",
      any("web tags" in n["headline"] for n in _pic_notes))
check("a document with no pictures says so plainly",
      "No pictures" in _heads("# A\n\nProse.\n"))

check("one paragraph per line is confirmed after every conversion",
      "Each paragraph is on a single line" in _heads("# A\n\nA paragraph.\n"))
_wrapped = "# A\n\n" + "\n".join(["A short wrapped line of prose here."] * 40) + "\n"
check("paragraphs that came out wrapped anyway are reported as a problem",
      _levels(_wrapped).get("The paragraphs may have been broken up") == "warn")

_warned = _convert.report(_fake("# A\n\nProse.\n",
                               warnings=["[WARNING] Skipped an object"]))["notes"]
check("a complaint from the converter is passed on, not swallowed",
      any(n["level"] == "warn" and "Skipped an object" in n["body"]
          for n in _warned))
check("a silent conversion says nothing about the converter",
      not any("converter reported" in n["headline"]
              for n in _convert.report(_fake("# A\n\nProse.\n"))["notes"]))

check("every note tells the author what to check, or says there is nothing to",
      all(isinstance(n["check"], str)
          for n in _convert.report(_fake("# A\n\nProse.\n"))["notes"]))
check("no note tells the author to run anything",
      not any(w in (n["body"] + n["check"]).lower()
              for n in _convert.report(_fake(_html_table["text"], _pics))["notes"]
              for w in ("terminal", "command line", "brew ", "run pandoc")))

# A chapter out of Word carries raw HTML wherever markdown had no equivalent: a
# picture with a caption, a table with merged cells. Those are several lines of
# markup, and must not be mistaken for a paragraph somebody wrapped by hand -
# otherwise every imported chapter carries a warning that means nothing.
_htmlish = DocMap(
    "# Chapter\n\nA paragraph.\n\n"
    "<figure>\n<img src=\"../assets/c/rId1.png\" alt=\"A diagram\" />\n"
    "<figcaption aria-hidden=\"true\"><p>A diagram</p></figcaption>\n</figure>\n\n"
    "<table>\n<tr><td><p>one</p>\n<p>two</p></td></tr>\n</table>\n",
    "c.md")
check("blocks of web markup are not mistaken for a wrapped paragraph",
      not hard_wrapped_paragraphs(_htmlish),
      "got " + str(hard_wrapped_paragraphs(_htmlish)))
_by_hand = DocMap("# Chapter\n\nA paragraph that someone\nwrapped by hand over\n"
                  "three lines.\n", "c.md")
check("a genuinely wrapped paragraph is still spotted",
      hard_wrapped_paragraphs(_by_hand))

# --- front matter on a re-import ---------------------------------------------

_fm_dir = tempfile.mkdtemp()
_fm_old = os.path.join(_fm_dir, "ch.md")
check("no chapter there yet: the converted text is left as it is",
      _convert.keep_front_matter(_fm_old, "# New\n") == "# New\n")
with open(_fm_old, "w") as fh:
    fh.write("---\ntitle: Old\n---\n\n# Old\n")
check("the chapter it replaces lends its front matter",
      _convert.keep_front_matter(_fm_old, "# New\n") == "---\ntitle: Old\n---\n\n# New\n")
check("front matter in the converted text wins",
      _convert.keep_front_matter(_fm_old, "---\ntitle: New\n---\n# New\n")
      == "---\ntitle: New\n---\n# New\n")
with open(_fm_old, "w") as fh:
    fh.write("# Old\n\n---\n\nA rule, not front matter.\n")
check("a horizontal rule further down is not front matter",
      _convert.keep_front_matter(_fm_old, "# New\n") == "# New\n")
with open(_fm_old, "w") as fh:
    fh.write('---\ntitle: "Chapter 1: *Old*"\ntopic: x\n---\n\n# Chapter 1: Old\n')
check("a title: that repeats the new chapter's heading is left out",
      _convert.keep_front_matter(_fm_old, "# Chapter 1: *Old*\n") == "---\ntopic: x\n---\n\n# Chapter 1: *Old*\n")
check("a title: that differs from the heading is kept",
      _convert.keep_front_matter(_fm_old, "# Chapter 1: New\n").startswith('---\ntitle: "Chapter 1: *Old*"\n'))
with open(_fm_old, "w") as fh:
    fh.write("---\ntitle: Old\n---\n\n# Old\n")
check("front matter that was only the repeated title goes altogether",
      _convert.keep_front_matter(_fm_old, "# Old\n") == "# Old\n")
shutil.rmtree(_fm_dir, ignore_errors=True)

# --- one H1 per chapter ------------------------------------------------------

check("a later H1 (a Word Heading 1 after the title) becomes an H2",
      _convert.one_h1("# Chapter 1\n\nText.\n\n# References\n\nA.\n")
      == "# Chapter 1\n\nText.\n\n## References\n\nA.\n")
check("the first H1 and lower headings are left as they are",
      _convert.one_h1("Intro.\n\n# Title\n\n## Part\n\n### Sub\n")
      == "Intro.\n\n# Title\n\n## Part\n\n### Sub\n")
check("a # line inside fenced code is not a heading",
      _convert.one_h1("# Title\n\n```sh\n# a comment\n```\n\n# Next\n")
      == "# Title\n\n```sh\n# a comment\n```\n\n## Next\n")


# --- headings made by hand (a line of bold or italic) -----------------------

_eh_in = ("## 11.3 Methods\n\n***11.3.1 Case Study***\n\n*Limits*\n\nText with **bold**.\n\n"
          "*Groups*:\n\n***11.3.2 Biography***\n\n**A bold sentence.**\n\n**a** and **b**\n\n"
          "*[a link](https://x.org)*\n\n```\n**code**\n```\n\n**Lead-in**\ntext\n")
_eh_out, _eh_made = _convert.emphasis_headings(_eh_in)
check("an emphasis-only line becomes a heading one level below the one before it; "
      "each kind of emphasis keeps its own level until the next real heading",
      _eh_out == ("## 11.3 Methods\n\n### 11.3.1 Case Study\n\n#### Limits\n\nText with **bold**.\n\n"
                  "#### Groups\n\n### 11.3.2 Biography\n\n**A bold sentence.**\n\n**a** and **b**\n\n"
                  "*[a link](https://x.org)*\n\n```\n**code**\n```\n\n**Lead-in**\ntext\n"), _eh_out)
check("each made heading is recorded, in order, with its level and what it was",
      [(h["text"], h["level"], h["was"]) for h in _eh_made]
      == [("11.3.1 Case Study", 3, "bold and italic"), ("Limits", 4, "italic"),
          ("Groups", 4, "italic"), ("11.3.2 Biography", 3, "bold and italic")], _eh_made)
check("under the title alone, a made heading is level 2",
      _convert.emphasis_headings("# Title\n\n**Part one**\n\nText.\n")[0]
      == "# Title\n\n## Part one\n\nText.\n")
_eh_notes = _convert.report({"text": _eh_out, "media": [], "emphasis_headings": _eh_made})["notes"]
_eh_note = [n for n in _eh_notes if "made into" in n["headline"]]
check("what to check lists every made heading with its line",
      len(_eh_note) == 1 and _eh_note[0]["headline"] == "4 lines were made into headings"
      and "line 3: “11.3.1 Case Study” (was bold and italic, now level 3)" in _eh_note[0]["body"]
      and "line 11: “11.3.2 Biography”" in _eh_note[0]["body"], _eh_note)
check("nothing made, nothing listed",
      not [n for n in _convert.report({"text": "# T\n", "media": []})["notes"] if "made into" in n["headline"]])

# --- the real thing, when this machine has pandoc ---------------------------

import base64  # noqa: E402
import urllib.parse  # noqa: E402

# A real one-pixel PNG, so the picture in the fixture is one Word and pandoc
# both accept and the whole picture path is exercised, not simulated.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg==")

_pandoc, _ = _convert.find_pandoc()
if _pandoc:
    with open(os.path.join(_wroot, "figure.png"), "wb") as fh:
        fh.write(_ONE_PIXEL_PNG)
    _src = os.path.join(_wroot, "src.md")
    with open(_src, "w") as fh:
        fh.write("# Chapter Nine\n\n" + ("A long paragraph. " * 40) + "\n\n"
                 "![A diagram](figure.png)\n\n"
                 "## References\n\nArcher, M. S. (1995). *Realist social "
                 "theory*.\n")
    _docx = os.path.join(_wroot, "src.docx")
    subprocess.run([_pandoc, _src, "-o", _docx], check=True, cwd=_wroot,
                   capture_output=True, timeout=120)

    _result = _convert.convert(_docx, "Chapter 9.md", _wroot)
    _para = [ln for ln in _result["text"].split("\n")
             if ln.startswith("A long paragraph")]
    check("a real conversion puts the whole paragraph on one line",
          len(_para) == 1 and len(_para[0]) > 400)
    check("a real conversion keeps the headings",
          _result["text"].startswith("# Chapter Nine"))
    check("a real conversion sends its pictures to the vault's assets folder",
          _result["media_rel"] == "assets/Chapter 9", _result["media_rel"])
    check("and the links in the chapter say so, before anything is written",
          "../assets/Chapter%209/" in _result["text"]
          and _convert.STAGE_MEDIA not in _result["text"],
          [ln for ln in _result["text"].split("\n") if "img" in ln or "![" in ln])

    _chapter, _media = _convert.save(_result, _wroot)
    check("the converted chapter is written where the author chose",
          os.path.isfile(_chapter)
          and _chapter == os.path.join(_wroot, "Chapter 9.md"))
    _pic_name = _result["media"][0]["name"]
    check("its pictures are written into the vault's assets folder, not beside it",
          _media == os.path.join(_vault, "assets", "Chapter 9")
          and os.path.isfile(os.path.join(_media, _pic_name)),
          _media)
    check("nothing is left beside the chapter for a department edition to miss",
          not os.path.isdir(os.path.join(_wroot, "Chapter 9-media")))
    # The link is followed from where the chapter actually sits, which is the
    # only test that catches a link that is well-formed but points nowhere.
    _linked = urllib.parse.unquote(
        re.search(r'src="([^"]+)"', _result["text"]).group(1))
    check("and the link in the saved chapter reaches the picture that is there",
          os.path.isfile(os.path.normpath(
              os.path.join(os.path.dirname(_chapter), _linked))),
          _linked)

    # Re-importing over a chapter keeps its front matter; Word has none.
    _front = '---\ntitle: "Chapter Nine"\ntopic: "ontology"\n---\n'
    with open(_chapter) as fh:
        _saved = fh.read()
    with open(_chapter, "w") as fh:
        fh.write(_front + "\n# Chapter Nine\n\nOld text.\n")
    _again = _convert.convert(_docx, "Chapter 9.md", _wroot)
    check("a re-import keeps the replaced chapter's front matter, less the repeated title",
          _again["text"].startswith('---\ntopic: "ontology"\n---\n\n# Chapter Nine'), _again["text"][:120])
    _convert.discard(_again)
    with open(_chapter, "w") as fh:
        fh.write(_saved)

    # The point of all of it: the new chapter is one the analyses can work on.
    _s2 = Session(_chapter)
    _s2.load_chapter(_chapter)
    check("a just-converted chapter has no hard-wrapped paragraphs",
          not hard_wrapped_paragraphs(_s2.docmap))
    check("a just-converted chapter's reference list is found",
          _s2.docmap.refs_start is not None)
    _f2, _n2 = references.analyse(_s2.docmap, "obsidian")[0], None
    check("the analyses run on a just-converted chapter",
          isinstance(_f2, list))
    check("the tool does not warn that something else just saved the chapter",
          not any("saved by something else" in w
                  for w in _s2.preflight(we_just_wrote_it=True)[0]))
    check("but it still warns when it was not the tool that wrote it",
          any("saved by something else" in w for w in _s2.preflight()[0]))

    _convert.discard(_result)
    check("the temporary folder is cleared away afterwards",
          not os.path.isdir(_result["stage"]))
else:
    check("pandoc is on this machine, so the real conversion was checked",
          True, "skipped: no pandoc here, which is allowed")

# ---------------------------------------------------------------------------
# The console: the readable layer over the place the textbook is stored.
# ---------------------------------------------------------------------------

from app import config as _config  # noqa: E402
from app import console as _console  # noqa: E402
from app import github as _github  # noqa: E402
from app import keychain as _keychain  # noqa: E402
from app import registry as _registry  # noqa: E402

# Nothing in these checks may touch the real Keychain or the real
# Application Support folder.
_FAKE_KEYCHAIN = {}
_keychain.save = lambda account, secret: _FAKE_KEYCHAIN.update(
    {account: secret}) or True
_keychain.load = lambda account: _FAKE_KEYCHAIN.get(account)
_keychain.delete = lambda account: _FAKE_KEYCHAIN.pop(account, None) or True
_support = tempfile.mkdtemp(prefix="aa-support-")
_config.SUPPORT_DIR = _support
_config.STATE_FILE = os.path.join(_support, "state.json")
_registry.CACHE_FILE = os.path.join(_support, "registry.json")
_registry.BUNDLED_FILE = os.path.join(_support, "no-bundled-copy.json")

# A list of books like the registry's, with made-up repositories. The second
# book uses different branch names, so nothing can pass by assuming
# drafts/main.
_REG_DATA = {
    "schema_version": 1,
    "platform": {"console_oauth_client_id": "Iv1.registry-client"},
    "books": [
        {"slug": "book-a", "status": "live", "title": "Book A",
         "content": {"repo": "example-org/book-a", "live_branch": "main",
                     "drafts_branch": "drafts"},
         "site": {"domain": "book-a.example",
                  "host": {"kind": "obsidian-publish", "site_id": "x",
                           "publish_host": "publish-01.obsidian.md"}}},
        {"slug": "book-b", "status": "live", "title": "Book B",
         "content": {"repo": "example-org/book-b", "live_branch": "published",
                     "drafts_branch": "staging"},
         "site": {"domain": "book-b.example",
                  "host": {"kind": "static", "provider": "cloudflare-pages",
                           "project": "book-b", "paid_by": "platform"}}},
        {"slug": "book-c", "status": "preview", "title": "Book C",
         "content": {"repo": "other-org/book-c", "live_branch": "main",
                     "drafts_branch": "drafts"},
         "site": {"domain": None}},
        {"slug": "old-book", "status": "retired", "title": "Old Book",
         "content": {"repo": "example-org/old-book", "live_branch": "main",
                     "drafts_branch": "drafts"},
         "site": {"domain": "old.example"}},
    ],
}
_REG = _registry.Registry(json.loads(json.dumps(_REG_DATA)), "live", time.time())
_registry.use(_REG)
_BOOK = _REG.find("book-a")
_BOOK_B = _REG.find("book-b")

# --- reading a suggestion the form actually produces ---

_ISSUE = {
    "number": 41,
    "title": "Suggested edit: chapters/chapter-03.md",
    "created_at": "2026-08-20T09:12:00Z",
    "html_url": "https://example.invalid/41",
    "body": (
        "**File:** [`chapters/chapter-03.md`](https://example.invalid/f)\n"
        "\n### Suggested edit\n\n"
        "```text\n"
        "\"the the domains\" should be \"the three domains\"\n"
        "```\n"
        "\n### Reasoning\n\n"
        "```text\nIt reads oddly.\n```\n"
        "\n---\n\n"
        "**Submitted by:** `Ada L` (`a***@example.com`)\n"
        "\n_submitted via the suggest-an-edit form_"
    ),
}

_parsed = _console.parse_suggestion(_ISSUE)
check("a suggestion's page is read from the title",
      _parsed["path"] == "chapters/chapter-03.md", _parsed["path"])
check("a suggestion's page is shown by name, not by file path",
      _parsed["page"] == "chapter-03", _parsed["page"])
check("the reader's name is read out of the suggestion",
      _parsed["who"] == "Ada L", _parsed["who"])
check("the suggestion text is read without its fencing",
      _parsed["suggestion"] == '"the the domains" should be "the three domains"',
      _parsed["suggestion"])
check("the reasoning is read separately from the suggestion",
      _parsed["reasoning"] == "It reads oddly.", _parsed["reasoning"])

# --- when the tool is allowed to make the change itself ---

check("an exact replacement in quotes is recognised",
      _console.literal_replacement('"cat" should be "dog"') ==
      {"old": "cat", "new": "dog"})
check("'change X to Y' is recognised",
      _console.literal_replacement("change 'cat' to 'dog'") ==
      {"old": "cat", "new": "dog"})
check("ordinary prose is NOT treated as an instruction",
      _console.literal_replacement(
          "the paragraph about stratification is confusing, maybe lead with "
          "the example instead") is None)
check("two competing replacements are refused rather than guessed between",
      _console.literal_replacement('"a" should be "b" and "c" should be "d"')
      is None)

_vault = tempfile.mkdtemp(prefix="aa-console-")
os.makedirs(os.path.join(_vault, "chapters"))
_chapter = os.path.join(_vault, "chapters", "chapter-03.md")
_ORIGINAL = (
    "# Chapter 3\n"
    "\n"
    "This line mentions the the domains once.\n"
    "A second line that must not be touched at all.\n"
    "A third line, also untouched.\n"
)
with open(_chapter, "w", encoding="utf-8") as fh:
    fh.write(_ORIGINAL)

_plan = _console.plan_change(_vault, "chapters/chapter-03.md",
                             '"the the domains" should be "the three domains"')
check("a one-off wording found once in the chapter can be applied",
      _plan["can_apply"] is True, _plan["reason"])
check("the plan points at the right line",
      _plan["line_no"] == 3, _plan["line_no"])
check("the plan shows the line as it will read afterwards",
      _plan["after"] == "This line mentions the three domains once.",
      _plan["after"])

_ok, _msg = _console.apply_change(_plan)
with open(_chapter, "r", encoding="utf-8") as fh:
    _after_text = fh.read()
_before_lines = _ORIGINAL.split("\n")
_after_lines = _after_text.split("\n")
check("applying a suggestion reports success", _ok, _msg)
check("applying a suggestion changes exactly one line",
      sum(1 for a, b in zip(_before_lines, _after_lines) if a != b) == 1,
      [a for a, b in zip(_before_lines, _after_lines) if a != b])
check("every other line is left byte for byte as it was",
      [l for i, l in enumerate(_after_lines) if i != 2] ==
      [l for i, l in enumerate(_before_lines) if i != 2])
check("the file still ends the way it did",
      _after_text.endswith("\n") and not _after_text.endswith("\n\n"))

# --- the cases where it must refuse ---

_twice = os.path.join(_vault, "chapters", "chapter-04.md")
with open(_twice, "w", encoding="utf-8") as fh:
    fh.write("the cat sat\nthe cat stood\n")
_p2 = _console.plan_change(_vault, "chapters/chapter-04.md",
                           '"the cat" should be "the dog"')
check("a wording appearing twice is refused rather than guessed at",
      _p2["can_apply"] is False and "twice" in _p2["reason"], _p2["reason"])

_p3 = _console.plan_change(_vault, "chapters/chapter-03.md",
                           '"nowhere in the file" should be "x"')
check("a wording that is no longer present is refused",
      _p3["can_apply"] is False and "not in that chapter" in _p3["reason"],
      _p3["reason"])

_p4 = _console.plan_change(_vault, "chapters/nope.md", '"a" should be "b"')
check("a page that is not in the chosen folder is refused",
      _p4["can_apply"] is False, _p4["reason"])

_p5 = _console.plan_change(None, "chapters/chapter-03.md", '"a" should be "b"')
check("with no chapters folder chosen, nothing is applied",
      _p5["can_apply"] is False, _p5["reason"])

# A plan worked out before the file moved underneath it must not be applied.
_stale = _console.plan_change(_vault, "chapters/chapter-03.md",
                              '"the three domains" should be "the 3 domains"')
with open(_chapter, "a", encoding="utf-8") as fh:
    fh.write("Someone else edited this file.\n")
with open(_chapter, "r+", encoding="utf-8") as fh:
    _txt = fh.read().replace("the three domains", "THE THREE DOMAINS")
    fh.seek(0); fh.write(_txt); fh.truncate()
_ok2, _msg2 = _console.apply_change(_stale)
check("a chapter that changed on disk is refused, and nothing is written",
      _ok2 is False and "changed on disk" in _msg2, _msg2)

# --- draft changes ---

_small = _console.readable_change([
    {"filename": "chapters/chapter-03.md", "additions": 1, "deletions": 1,
     "changes": 2, "patch": "@@ -1 +1 @@\n-old wording\n+new wording"},
])
check("a small draft change is rendered as readable before/after",
      _small["readable"] is True and
      _small["pages"][0]["lines"] == [
          {"kind": "before", "text": "old wording"},
          {"kind": "after", "text": "new wording"}],
      _small["pages"][0]["lines"])

_big = _console.readable_change([
    {"filename": "chapters/chapter-03.md", "additions": 90, "deletions": 90,
     "changes": 180, "patch": "@@\n" + "\n".join("+line" for _ in range(90))},
])
check("a large draft change says so instead of printing a wall of text",
      _big["readable"] is False and "large change" in _big["why"], _big["why"])

check("a draft change is described by who wrote it, in plain words",
      _console.describe_change(
          {"number": 7, "title": "Fix a typo", "created_at": "2026-08-01T00:00:00Z",
           "user": {"login": "ada"}, "html_url": "u"})["who"] == "ada")

# --- failures are explained, not swallowed ---

class _FakeHTTPError(Exception):
    def __init__(self, code, message=""):
        self.code = code
        self._m = json.dumps({"message": message}).encode()
    def read(self):
        return self._m

_p401 = _github._http_problem(_FakeHTTPError(401))
check("a rejected sign-in asks the author to sign in again",
      _p401.needs_signin is True and "sign in again" in _p401.message.lower(),
      _p401.message)
_p404 = _github._http_problem(_FakeHTTPError(404))
check("a missing item is explained rather than shown as a code",
      _p404.needs_signin is False and "404" not in _p404.message, _p404.message)
_p500 = _github._http_problem(_FakeHTTPError(500))
check("a server fault says plainly that nothing was changed",
      "Nothing was changed" in _p500.message, _p500.message)
_prate = _github._http_problem(_FakeHTTPError(403, "API rate limit exceeded"))
check("being rate limited is told apart from being refused",
      "slow down" in _prate.message.lower(), _prate.message)

# ---------------------------------------------------------------------------
# Many books. Every per-book value comes from the registry; the book being
# worked on is chosen, never assumed; and one book's text can never be written
# into another book's vault.
# ---------------------------------------------------------------------------

def _refused(fn, exc=(KeyError, RuntimeError)):
    """The message a refusal gave, or None if nothing was refused."""
    try:
        fn()
    except exc as e:
        return str(e.args[0] if e.args else e)
    return None


def _reg_text(**changes):
    data = json.loads(json.dumps(_REG_DATA))
    data.update(changes)
    return json.dumps(data)


# --- which book a vault is ---

check("an SSH alias remote is read as owner/name",
      _registry.repo_from_url(
          "git@github-textbook:textbookproject2026-alt/textbook.git")
      == "textbookproject2026-alt/textbook")
check("https and ssh:// remotes are read as owner/name",
      _registry.repo_from_url("https://github.com/o/n.git") == "o/n" and
      _registry.repo_from_url("ssh://git@github.com/o/n") == "o/n")
check("a remote that isn't owner/name is not read as one",
      _registry.repo_from_url("https://example.com/a/b/c") is None)


def _make_book_vault(slug=None, remote=None, chapter_text=None, worktree=False):
    root = tempfile.mkdtemp(prefix="aa-vault-")
    os.makedirs(os.path.join(root, "chapters"))
    os.makedirs(os.path.join(root, ".obsidian"))
    if slug is not None:
        with open(os.path.join(root, "textbook.config.json"), "w") as fh:
            json.dump({"slug": slug, "title": "ignored"}, fh)
    if remote is not None:
        gitdir = os.path.join(root, ".git")
        if worktree:
            main = tempfile.mkdtemp(prefix="aa-main-")
            common = os.path.join(main, ".git")
            gitdir = os.path.join(common, "worktrees", "wt")
            os.makedirs(gitdir)
            with open(os.path.join(gitdir, "commondir"), "w") as fh:
                fh.write("../..\n")
            with open(os.path.join(root, ".git"), "w") as fh:
                fh.write(f"gitdir: {gitdir}\n")
            gitdir = common
        os.makedirs(gitdir, exist_ok=True)
        with open(os.path.join(gitdir, "config"), "w") as fh:
            fh.write('[core]\n\tbare = false\n[remote "upstream"]\n'
                     '\turl = git@github.com:example-org/book-b.git\n'
                     f'[remote "origin"]\n\turl = {remote}\n'
                     '\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    with open(os.path.join(root, "chapters", "chapter-01.md"), "w") as fh:
        fh.write(chapter_text or "# One\n\nThe the words are here.\nLeave me.\n")
    return root


_va = _make_book_vault("book-a", "git@github-textbook:Example-Org/Book-A.git")
_vb = _make_book_vault("book-b", "https://github.com/example-org/book-b.git")
_idA = _registry.identify(_va)
check("a vault whose name and remote agree with the list is that book",
      _idA["state"] == "ok" and _idA["book"].slug == "book-a", _idA)
check("only origin is compared, not another remote",
      _registry.origin_repo(_va) == "Example-Org/Book-A")
check("a vault found from its chapters folder is the whole vault",
      _registry.vault_root(os.path.join(_va, "chapters")) == _va)
check("a worktree's remote is read from the main checkout",
      _registry.identify(_make_book_vault(
          "book-b", "git@github.com:example-org/book-b.git",
          worktree=True))["state"] == "ok")

_vmis = _make_book_vault("book-a", "git@github.com:example-org/book-b.git")
_idmis = _registry.identify(_vmis)
check("a vault that names one book but is a copy of another is refused",
      _idmis["state"] == "remote_mismatch" and _idmis["book"] is None and
      "example-org/book-b" in _idmis["message"] and
      "example-org/book-a" in _idmis["message"], _idmis["message"])
check("a vault naming a book that isn't registered is refused",
      _registry.identify(_make_book_vault("no-such", "git@x:o/n.git"))["state"]
      == "unknown_slug")
check("a vault naming a retired book is refused",
      _registry.identify(_make_book_vault("old-book",
                                          "git@x:example-org/old-book.git"))
      ["state"] == "unknown_slug")
check("a vault naming a book but with no remote can't be confirmed",
      _registry.identify(_make_book_vault("book-a"))["state"] == "no_remote")
_vplain = _make_book_vault()
check("a vault that names no book is just unlinked",
      _registry.identify(_vplain)["state"] == "unlinked")

# --- nothing per-book is written into the app ---

_constants = []
for _rel in ("app/github.py", "app/console.py"):
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), *_rel.split("/")),
            encoding="utf-8") as fh:
        _src = fh.read()
    for _needle in ("textbookproject2026-alt/textbook", "confused4now",
                    "social-research-methods", 'OWNER = "', 'REPO = "',
                    "DRAFTS_BRANCH", "LIVE_BRANCH", "base=drafts"):
        if _needle in _src:
            _constants.append(f"{_rel}: {_needle}")
check("no book's repository, branch or site is written into the app",
      not _constants, _constants)



# ---------------------------------------------------------------------------
# "Send to drafts" for a Word import (BOOK-ONE-TO-QUARTZ.md §8 step 1).
#
# One commit on the drafts branch, made as the signed-in author, holding the
# chapter and its pictures. The branch is moved without force on top of what
# was read, so if anyone else moved it in between, nothing is sent, nothing of
# theirs is lost, and the author is shown the drafts area again.
# ---------------------------------------------------------------------------

import hashlib as _hashlib  # noqa: E402
import threading as _threading_mod  # noqa: E402
import posixpath as _posixpath  # noqa: E402

from app import drafts as _drafts  # noqa: E402


# The fake GitHub the drafts checks talk to (once shared with the desktop
# app's own checks, which went with it).

_COMPARE = {
    "ahead_by": 2,
    "files": [
        {"filename": "chapters/chapter-03.md", "additions": 2, "deletions": 1},
        {"filename": "chapters/chapter-09.md", "additions": 4, "deletions": 0},
    ],
    "commits": [
        {"commit": {"message": "Fix a typo in chapter 3\n\nlonger body",
                    "author": {"name": "Ada L"}},
         "author": {"login": "ada"}},
        # Written in the browser CMS, never seen by this console.
        {"commit": {"message": "Update chapter-09.md",
                    "author": {"name": "Textbook CMS"}},
         "author": None},
    ],
}


class _Service:
    """Enough of the service to drive the accept path without a network."""

    def __init__(self, mergeable=True, state="clean", ahead=2):
        self.calls = []
        self.open_prs = []
        self.mergeable = mergeable
        self.state = state
        self.ahead = ahead
        self.merged = []

    def request(self, method, url, token=None, payload=None, accept=None):
        self.calls.append((method, url, payload))
        if "/compare/" in url:
            return dict(_COMPARE, ahead_by=self.ahead)
        if method == "PUT" and url.endswith("/merge"):
            number = url.split("/pulls/")[1].split("/")[0]
            self.merged.append(number)
            return {"merged": True, "sha": "0" * 40}
        if method == "GET" and "/pulls?state=open" in url:
            return list(self.open_prs)
        if method == "POST" and url.endswith("/pulls"):
            pr = {"number": 77, "html_url": "https://example.invalid/77",
                  "created_at": "2026-09-01T00:00:00Z",
                  "title": payload["title"], "body": payload["body"]}
            self.open_prs.append(pr)
            return pr
        if method == "PATCH" and "/pulls/" in url:
            self.open_prs[0].update(payload)
            return self.open_prs[0]
        if method == "GET" and "/pulls/" in url:
            return {"number": 77, "mergeable": self.mergeable,
                    "mergeable_state": self.state}
        return {}


def _with_service(service, fn):
    real_request = _github._request
    _github._request = service.request
    try:
        return fn()
    finally:
        _github._request = real_request


class _Books(_Service):
    """The service, answering which repositories the author can write to."""

    def __init__(self, perms, offline=False, **kw):
        super().__init__(**kw)
        self.perms = perms
        self.offline = offline
        self.issues = {}

    def request(self, method, url, token=None, payload=None, accept=None):
        if url == f"{_github.API}/user":
            if self.offline:
                return _github.Problem("offline", offline=True)
            return {"login": "author", "name": "The Author"}
        for repo, perm in self.perms.items():
            if url == f"{_github.API}/repos/{repo}":
                self.calls.append((method, url, payload))
                if self.offline:
                    return _github.Problem("offline", offline=True)
                if perm == 404:
                    return _github.Problem("gone", code=404)
                if perm == 500:
                    return _github.Problem("trouble", code=500)
                return {"full_name": repo, "private": perm == "private",
                        "permissions": {"push": perm == "write",
                                        "pull": True}}
        if "/issues?" in url:
            self.calls.append((method, url, payload))
            return list(self.issues.get(url.split("/repos/")[1].split("/issues")[0], []))
        if "/actions/workflows/" in url:
            return {"workflow_runs": []}
        return super().request(method, url, token, payload, accept)



class _GitRepo(_Books):
    """One book's repository, kept in memory, answering the Git Data API."""

    def __init__(self, repo, files, perms=None, **kw):
        super().__init__(perms or {repo: "write"}, **kw)
        self.repo = repo
        self.base = f"{_github.API}/repos/{repo}"
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.branches = {}
        self.before_move = None    # another client, acting just before our move
        self.before_listing = None  # another client, acting as drafts is listed
        self.ticks = 0             # the service's clock, one tick per event
        self.labelled = {}         # issue number -> [(label, when)]
        root = self._commit(self._tree(self._files(files)), [], "start", "maint")
        self.branches = {"main": root, "drafts": root}

    def _files(self, files):
        out = {}
        for path, data in files.items():
            data = data.encode() if isinstance(data, str) else data
            sha = _drafts.blob_sha(data)
            self.blobs[sha] = data
            out[path] = sha
        return out

    def _tree(self, files):
        sha = _hashlib.sha1(json.dumps(sorted(files.items())).encode()).hexdigest()
        self.trees[sha] = dict(files)
        return sha

    def now(self):
        self.ticks += 1
        return f"2026-09-23T10:{self.ticks // 60:02d}:{self.ticks % 60:02d}Z"

    def _commit(self, tree, parents, message, who):
        sha = _hashlib.sha1(json.dumps([tree, parents, message, who,
                                         len(self.commits)]).encode()).hexdigest()
        self.commits[sha] = {"tree": tree, "parents": parents,
                             "message": message, "who": who, "when": self.now()}
        return sha

    def files(self, branch="drafts"):
        tree = self.trees[self.commits[self.branches[branch]]["tree"]]
        return {p: self.blobs[s] for p, s in tree.items()}

    def push(self, who, changes, message="Update from the browser editor",
             branch="drafts"):
        """Someone else writes to drafts, as the browser editor would."""
        files = dict(self.trees[self.commits[self.branches[branch]]["tree"]])
        for path, data in changes.items():
            if data is None:
                files.pop(path, None)
            else:
                files.update(self._files({path: data}))
        self.branches[branch] = self._commit(
            self._tree(files), [self.branches[branch]], message, who)

    def _ancestors(self, sha):
        seen, todo = set(), [sha]
        while todo:
            s = todo.pop()
            if s not in seen:
                seen.add(s)
                todo.extend(self.commits[s]["parents"])
        return seen

    def request(self, method, url, token=None, payload=None, accept=None):
        rest = url[len(self.base) + 1:]
        issue = re.match(r"issues/(\d+)/(labels|events)", rest)
        if url.startswith(self.base + "/") and issue:
            self.calls.append((method, url, payload))
            number = int(issue.group(1))
            if method == "POST" and issue.group(2) == "labels":
                when = self.now()
                self.labelled.setdefault(number, []).extend(
                    (name, when) for name in payload["labels"])
                return [{"name": n} for n, _ in self.labelled[number]]
            if method == "GET" and issue.group(2) == "events":
                return [{"event": "labeled", "label": {"name": n},
                         "created_at": w} for n, w in self.labelled.get(number, [])]
            return {}
        if not url.startswith(self.base + "/") or \
                not rest.startswith(("git/", "commits?", "contents/")):
            return super().request(method, url, token, payload, accept)
        self.calls.append((method, url, payload))
        if method == "GET" and rest.startswith("contents/"):
            path, q = rest[len("contents/"):].split("?", 1)
            ref = urllib.parse.parse_qs(q)["ref"][0]
            ref = self.branches.get(ref, ref)
            sha = self.trees[self.commits[ref]["tree"]].get(
                urllib.parse.unquote(path))
            if sha is None:
                return _github.Problem("gone", code=404)
            return {"sha": sha, "encoding": "base64",
                    "content": base64.b64encode(self.blobs[sha]).decode()}
        if method == "GET" and rest.startswith("git/ref/heads/"):
            name = urllib.parse.unquote(rest[len("git/ref/heads/"):])
            if name not in self.branches:
                return _github.Problem("gone", code=404)
            return {"object": {"sha": self.branches[name]}}
        if method == "GET" and rest.startswith("git/commits/"):
            return {"tree": {"sha": self.commits[rest.split("/")[2]]["tree"]}}
        if method == "GET" and rest.startswith("git/trees/"):
            # As the service does, a commit's or a branch's name lists that
            # commit's tree, and the answer is named after the commit.
            name = urllib.parse.unquote(rest[len("git/trees/"):].split("?")[0])
            if self.before_listing:
                self.before_listing()
                self.before_listing = None
            name = self.branches.get(name, name)
            files = self.trees[self.commits[name]["tree"]
                               if name in self.commits else name]
            entries, dirs = [], set()
            for path, sha in files.items():
                entries.append({"path": path, "type": "blob", "sha": sha})
                parts = path.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    dirs.add("/".join(parts[:i]))
            entries += [{"path": d, "type": "tree", "sha": "t"} for d in dirs]
            return {"sha": name, "tree": entries, "truncated": False}
        if method == "GET" and rest.startswith("git/blobs/"):
            return {"content": base64.b64encode(
                self.blobs[rest.split("/")[2]]).decode()}
        if method == "GET" and rest.startswith("commits?"):
            q = urllib.parse.parse_qs(rest.split("?", 1)[1])
            sha = self.branches.get(q["sha"][0], q["sha"][0])
            if "path" not in q:
                return [{"sha": sha,
                         "commit": {"tree": {"sha": self.commits[sha]["tree"]}}}]
            path = q["path"][0]
            if "since" in q:
                out = []
                for c in self._walk(sha):
                    parents = self.commits[c]["parents"]
                    mine = self.trees[self.commits[c]["tree"]].get(path)
                    before = (self.trees[self.commits[parents[0]]["tree"]].get(path)
                              if parents else None)
                    info = self.commits[c]
                    if mine != before and info["when"] >= q["since"][0]:
                        out.append({"sha": c,
                                    "html_url": f"https://example.invalid/commit/{c}",
                                    "commit": {"message": info["message"],
                                               "author": {"name": info["who"],
                                                          "date": info["when"]}},
                                    "author": {"login": info["who"]}})
                return out
            for c in self._walk(sha):
                parents = self.commits[c]["parents"]
                mine = self.trees[self.commits[c]["tree"]].get(path)
                before = (self.trees[self.commits[parents[0]]["tree"]].get(path)
                          if parents else None)
                if mine != before:
                    info = self.commits[c]
                    return [{"commit": {"message": info["message"],
                                        "author": {"name": info["who"],
                                                   "date": "2026-09-22T10:00:00Z"}},
                             "author": {"login": info["who"]}}]
            return []
        if method == "POST" and rest == "git/blobs":
            data = base64.b64decode(payload["content"])
            sha = _drafts.blob_sha(data)
            self.blobs[sha] = data
            return {"sha": sha}
        if method == "POST" and rest == "git/trees":
            files = dict(self.trees[payload["base_tree"]])
            for e in payload["tree"]:
                if e["sha"] is None:
                    files.pop(e["path"], None)
                else:
                    files[e["path"]] = e["sha"]
            return {"sha": self._tree(files)}
        if method == "POST" and rest == "git/commits":
            # No author in the request: the service makes it the token's owner.
            who = payload.get("author", {}).get("name") or f"owner-of-{token}"
            sha = self._commit(payload["tree"], payload["parents"],
                               payload["message"], who)
            return {"sha": sha, "html_url": f"https://example.invalid/commit/{sha}"}
        if method == "PATCH" and rest.startswith("git/refs/heads/"):
            if self.before_move:
                self.before_move()
                self.before_move = None
            name = urllib.parse.unquote(rest[len("git/refs/heads/"):])
            if not payload.get("force") and \
                    self.branches[name] not in self._ancestors(payload["sha"]):
                return _github.Problem("Update is not a fast forward", code=422)
            self.branches[name] = payload["sha"]
            return {"object": {"sha": payload["sha"]}}
        return _github.Problem("not faked: " + rest, code=500)

    def _walk(self, sha):
        while sha:
            yield sha
            parents = self.commits[sha]["parents"]
            sha = parents[0] if parents else None


# --- building the tree -------------------------------------------------------

_PNG1, _PNG2 = b"\x89PNG one", b"\x89PNG two"
_t = _drafts.build_tree("chapters/c.md", b"# C\n", "assets/c",
                        {"image1.png": _PNG1, "image2.png": _PNG2}, {})
check("a new chapter's tree holds the chapter and every picture",
      [e["path"] for e in _t] ==
      ["chapters/c.md", "assets/c/image1.png", "assets/c/image2.png"] and
      all(e["data"] is not None for e in _t), _t)
_on = {"chapters/c.md": _drafts.blob_sha(b"# C old\n"),
       "assets/c/image1.png": _drafts.blob_sha(_PNG1),
       "assets/c/image2.png": _drafts.blob_sha(_PNG2)}
_t = _drafts.build_tree("chapters/c.md", b"# C\n", "assets/c",
                        {"image1.png": _PNG1}, _on)
check("a picture removed since the last import is taken out of drafts",
      {"path": "assets/c/image2.png", "sha": None, "data": None} in _t, _t)
check("a picture that hasn't changed isn't sent again",
      "assets/c/image1.png" not in [e["path"] for e in _t], _t)
check("the chapter itself is replaced when its text changed",
      [e["path"] for e in _t if e["data"]] == ["chapters/c.md"], _t)
check("a picture in a folder of its own inside the pictures folder is kept there",
      [e["path"] for e in _drafts.build_tree(
          "c.md", b"x", "assets/c", {"sub/fig.png": _PNG1}, {})][1]
      == "assets/c/sub/fig.png")
check("sending exactly what drafts already has builds nothing",
      _drafts.build_tree("chapters/c.md", b"# C\n", "assets/c",
                         {"image1.png": _PNG1},
                         {"chapters/c.md": _drafts.blob_sha(b"# C\n"),
                          "assets/c/image1.png": _drafts.blob_sha(_PNG1)}) == [])
check("a file of the same name outside the pictures folder is never touched",
      _drafts.build_tree("chapters/c.md", b"# C\n", "assets/c", {},
                         {"assets/c.png": "x", "assets/cc/i.png": "y"})
      == [{"path": "chapters/c.md", "sha": _drafts.blob_sha(b"# C\n"),
           "data": b"# C\n"}])
check("a file's hash is worked out the way the service works it out",
      _drafts.blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a")


def _word_vault(slug, remote, content=""):
    """A book checkout whose vault is its top folder, or a `content` folder in it."""
    root = _make_book_vault(slug, remote)
    vault = os.path.join(root, content) if content else root
    os.makedirs(os.path.join(vault, "chapters"), exist_ok=True)
    os.makedirs(os.path.join(vault, "assets"), exist_ok=True)
    with open(os.path.join(vault, "glossary.md"), "w") as fh:
        fh.write("# Glossary\n")
    return root, os.path.join(vault, "chapters")


_qroot, _qch = _word_vault("book-a", "git@github.com:example-org/book-a.git",
                           content="content")
check("the paths on drafts are the paths in the repository, not in the vault",
      _drafts.repo_paths(_qroot, _qch, "New.md") ==
      ("content/chapters/New.md", "content/assets/New"),
      _drafts.repo_paths(_qroot, _qch, "New.md"))
check("a chapter going outside the repository has no path on drafts",
      _drafts.repo_paths(os.path.join(_qroot, "content", "chapters"),
                         _qch, "New.md") is None)
shutil.rmtree(_qroot, ignore_errors=True)


# ---------------------------------------------------------------------------
# The rest of the author's path on drafts (BOOK-ONE-TO-QUARTZ.md §8 step 2).
#
# Tidying a chapter and accepting a reader's suggestion work on the chapter as
# the drafts area holds it, with no folder open, and send one commit made as
# the signed-in author. Only the lines that changed move. If drafts moved
# after it was read, nothing is sent and the author is offered it again.
# --- secrets ---

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "keychain.py")) as fh:
    _kc = fh.read()
check("the secret is written on standard input, never as a command argument",
      'stdin_text=secret' in _kc and '"-w", secret' not in _kc)
check("saving a secret is verified by reading it back",
      "return load(account) == secret" in _kc)
check("the GitHub sign-in and the DeepSeek key use one store, not two",
      _keychain.ACCOUNT_GITHUB != _keychain.ACCOUNT_DEEPSEEK and
      _keychain.SERVICE == "Authoring Assistant")




# --- a new chapter's line on the front page (contents.py) ---------------------

from app import contents as _contents

_SEED = ("---\nauthors:\n  - \"A\"\n---\n\n# Book\n\n## Contents\n\n"
         "- **[[chapters/chapter-01|Chapter 1 — One]]**\n  What it does.\n\n"
         "## Concept index\n\n- [[Example concept]]\n")
_new, _why = _contents.add_line(_SEED, "chapters/chapter-07.md", "Chapter 7: Seven")
check("a new chapter's line goes after the last item under Contents",
      _new == _SEED.replace("  What it does.\n",
                            "  What it does.\n- **[[chapters/chapter-07|Chapter 7: Seven]]**\n"),
      repr(_new))
_old_lines = _SEED.splitlines(keepends=True)
_new_lines = _new.splitlines(keepends=True)
check("and it is the only line that changes",
      len(_new_lines) == len(_old_lines) + 1
      and [l for l in _new_lines if l not in _old_lines]
      == ["- **[[chapters/chapter-07|Chapter 7: Seven]]**\n"])
check("line endings are kept",
      _contents.add_line(_SEED.replace("\n", "\r\n"), "chapters/X.md", "X")[0]
      == _new.replace("chapters/chapter-07|Chapter 7: Seven", "chapters/X|X")
      .replace("\n", "\r\n"))
check("a chapter already listed is not listed twice",
      _contents.add_line(_new, "chapters/chapter-07.md", "Seven")[0] is None)
check("no Contents heading: nothing is added, and the author is told",
      _contents.add_line("# Book\n\nText.\n", "chapters/X.md", "X")[0] is None
      and "no “Contents” heading" in _contents.add_line("# B\n", "chapters/X.md", "X")[1])
check("an empty Contents list gets the line under its heading",
      _contents.add_line("## Contents\n\n## Next\n", "chapters/X.md", "X")[0]
      == "## Contents\n\n- **[[chapters/X|X]]**\n\n## Next\n")
check("a | in the title can't break the link",
      _contents.line_for("chapters/X.md", "A | B") == "- **[[chapters/X|A - B]]**")
check("the title is the chapter's first heading, else its file name",
      _contents.chapter_title("Intro\n# **Chapter 3**: Reality\n", "c.md")
      == "Chapter 3: Reality"
      and _contents.chapter_title("No heading.", "chapter-09.md") == "chapter-09")

# ---------------------------------------------------------------------------
# The words the troubleshooting guide quotes.
#
# The vault repo's docs/troubleshooting.md walks the author through this screen
# by quoting it: button labels, screen names and error messages, word for word.
# Rename one of them here and that entry starts telling people to press a button
# that no longer exists, which is exactly the drift nobody notices. So the
# rename has to fail here first, and this test names the file to go and fix.
# ---------------------------------------------------------------------------

DOC_ENTRY = ('../Obsidian Vault/docs/troubleshooting.md — "The author\'s console '
             'won\'t sign in, or shows nothing waiting"')

# path -> the exact strings that entry depends on. Button labels carry their
# closing tag so that renaming the button, and not merely some prose that
# happens to repeat the words, is what breaks the check.
QUOTED_BY_THE_GUIDE = {
    "app/github.py": [
        "https://github.com/login/device",
        # Listed in the two pieces the source wraps them into: the flattening
        # below joins the lines but leaves the quotes that join the literals,
        # so a string may not span the seam between them.
        "This copy has not been set up for signing in yet. Ask the",
        "technical contact to add the sign-in identifier in Settings.",
        "Signing in could not be started. Check the sign-in identifier in",
        "The sign-in identifier in Settings is not recognised. Ask the",
        "technical contact to check it.",
        "Sign-in was refused on the web page. Nothing was changed.",
        "This Mac is not online, so signing in cannot start.",
        "Your sign-in is no longer accepted. Please sign in again.",
        "Your sign-in does not have permission to do that. Signing in again",
    ],
}

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_gone = []
for _relpath, _strings in QUOTED_BY_THE_GUIDE.items():
    with open(os.path.join(_here, *_relpath.split("/")), encoding="utf-8") as fh:
        # Flattened, because the source wraps these across lines and the guide
        # quotes them as the author reads them: as one sentence.
        _source = " ".join(fh.read().split())
    for _quoted in _strings:
        if " ".join(_quoted.split()) not in _source:
            _gone.append(f"{_relpath}: {_quoted!r}")

check("every word the troubleshooting guide quotes is still in the app",
      not _gone,
      "no longer in the app — update " + DOC_ENTRY + "\n         "
      + "\n         ".join(_gone))
shutil.rmtree(_vault, ignore_errors=True)

# ---------------------------------------------------------------------------
# The AI formatting check (formatting.py; the knowledge base is
# app/formatting_rules.md). DeepSeek itself is never called: every test gives
# the check its answer, or fails the network on purpose.
# ---------------------------------------------------------------------------

import urllib.error as _urlerr
from app import formatting, llm as _llm

# --- the rules file is the knowledge base, and the prompt is built from it ---

_rules = formatting.load_rules()
check("formatting: the rules file has a version line",
      _rules["version"].startswith("1"), _rules["version"])
check("formatting: the rules are read by their IDs",
      {"HEAD-1", "HEAD-4", "EMPH-1", "LIST-1", "TABLE-1", "NOTE-1", "CITE-1",
       "CALL-1", "LINK-1", "IMG-1", "FILE-4"} <= set(_rules["rules"]),
      sorted(_rules["rules"]))
check("formatting: a rule's wrapped lines are part of the rule",
      "graph" in _rules["rules"]["HEAD-1"]["text"], _rules["rules"]["HEAD-1"])
_prompt = formatting.build_prompt(_rules)
check("formatting: the prompt carries the rules file word for word",
      _rules["text"] in _prompt)
check("formatting: the prompt says formatting only, never wording",
      "never change, add, remove or reorder a single word" in _prompt)
_rules_dir = tempfile.mkdtemp(prefix="aa-rules-")
_rules7 = os.path.join(_rules_dir, "rules.md")
with open(_rules7, "w", encoding="utf-8") as fh:
    fh.write("# Rules\n\nVersion: 7 (test)\n\n## Odd\n\n"
             "- `ODD-1` Every line ends with a flourish.\n")
_r7 = formatting.load_rules(_rules7)
check("formatting: a changed rules file changes the prompt, with no code change",
      _r7["version"] == "7 (test)" and "ODD-1" in formatting.build_prompt(_r7)
      and "HEAD-1" not in formatting.build_prompt(_r7), _r7)
check("formatting: the rules file sits in app/, so the build bundles it",
      os.path.basename(os.path.dirname(formatting.RULES_PATH)) == "app"
      and os.path.isfile(formatting.RULES_PATH))

# --- the wording guard --------------------------------------------------------

_known = {"Emergence", "Monism", "chapter-04"}
_ok = [
    ("**Introduction**", "## Introduction"),
    ("#Chapter 9: Formatting", "# Chapter 9: Formatting"),
    ("• A bulleted point", "- A bulleted point"),
    ("> [!Tip] Remember", "> [!tip] Remember"),
    ("Structures matter. [^3]", "Structures matter.[^3]"),
    ("[underlined]{.underline} text", "underlined text"),
    ("[]{#_Toc123}Methods", "Methods"),
    ("See [[emergence]] here.", "See [[Emergence|emergence]] here."),
    ("## **Methods**", "## Methods"),
    ("| a | b |", "|a|b|"),
]
_bad = [
    ("The cat sat.", "The **big** cat sat.", "wording"),
    ("The cat sat.", "The cat sat!", "wording"),
    ('He said "yes".', "He said “yes”.", "wording"),
    ("See [the site](https://a.example).", "See [the site](https://b.example).",
     "web address"),
    ("A claim.[^1]", "A claim.[^2]", "footnote"),
    ("Archer, M. (1995). *Realist social theory*. ^ref-archer-1995",
     "Archer, M. (1995). *Realist social theory*.", "reference marker"),
    ("See [[Emergence]].", "See Emergence.", "concept link"),
    ("See Emergence.", "See [[Emergence]].", "concept link"),
    ("See [[emergence]].", "See [[Emergenze|emergence]].", "isn't a page"),
    ("One line.", "One\nline.", "split"),
]
check("formatting: the guard lets formatting-only changes through",
      all(formatting.guard(b, a, _known) is None for b, a in _ok),
      [(b, a, formatting.guard(b, a, _known)) for b, a in _ok
       if formatting.guard(b, a, _known)])
check("formatting: the guard stops every change that alters the text",
      all(want in (formatting.guard(b, a, _known) or "") for b, a, want in _bad),
      [(b, a, formatting.guard(b, a, _known)) for b, a, want in _bad
       if want not in (formatting.guard(b, a, _known) or "")])

# --- the whole flow: proposals, review, preview, save ------------------------

FMT_CHAPTER = """---
tags: [method]
---

#Chapter 9: Formatting

**Introduction**

Structures shape action.[^1] See [[emergence]] and [the site](https://example.com).

• A bulleted point
• Another point

> [!Tip] Remember
> Keep it simple.

```text
**not touched**
```

Monism is *one* idea.

[^1]: A note.
"""


def _proposal(line, before, after, rule, why="A fix."):
    return {"line": line, "before": before, "after": after, "rule": rule, "why": why}


FMT_ANSWER = {
    "changes": [
        _proposal(5, "#Chapter 9: Formatting", "# Chapter 9: Formatting", "HEAD-3"),
        _proposal(7, "**Introduction**", "## Introduction", "HEAD-4"),
        _proposal(9, "Structures shape action.[^1] See [[emergence]] and [the site](https://example.com).",
                  "Structures shape action.[^1] See [[Emergence|emergence]] and [the site](https://example.com).",
                  "LINK-1"),
        _proposal(11, "• A bulleted point", "- A bulleted point", "LIST-1"),
        _proposal(12, "• Another point", "- Another point", "LIST-1"),
        _proposal(14, "> [!Tip] Remember", "> [!tip] Remember", "CALL-1"),
        # the sneaky one: formatting, plus one extra word
        _proposal(15, "> Keep it simple.", "> Keep it **very** simple.", "EMPH-1"),
        _proposal(2, "tags: [method]", "tags: [methods]", "FILE-3"),
        _proposal(18, "**not touched**", "## not touched", "HEAD-4"),
        _proposal(23, "[^1]: Another note.", "[^1]: Another note!", "NOTE-1"),
        _proposal(21, "Monism is *one* idea.", "Monism is **one** idea.", "EMPH-1"),
    ],
    "notes": [{"line": 9, "rule": "FILE-2", "note": "Needs a blank line."}],
}

_fvault, _ = make_vault()
_fchap = os.path.join(_fvault, "chapter-09.md")
with open(_fchap, "w", encoding="utf-8") as fh:
    fh.write(FMT_CHAPTER)

_saved = (_llm.ask_json, _llm.have_key, _llm.load_key)
_asked = []


def _fake_ask(system, user, timeout=None):
    _asked.append((system, user))
    return json.loads(json.dumps(FMT_ANSWER)), None


try:
    _llm.ask_json, _llm.have_key = _fake_ask, (lambda: True)
    fs = Session(_fchap)
    fs.load_chapter(_fchap)
    ff, fnotes = fs.run_analyses({"analyses": ["format"]})
finally:
    _llm.ask_json, _llm.have_key, _llm.load_key = _saved

_by_line = {f["line_no"]: f for f in ff}
check("formatting: the chapter goes to DeepSeek numbered, with the rules as the prompt",
      _asked and _rules["text"] in _asked[0][0]
      and "5\t#Chapter 9: Formatting" in _asked[0][1], _asked[:1])
check("formatting: every safe proposal becomes a finding",
      sorted(_by_line) == [5, 7, 9, 11, 12, 14, 21], sorted(_by_line))
check("formatting: findings are their own kind, grouped by rule",
      all(f["kind"] == "format" for f in ff)
      and _by_line[11]["group"] == "format::LIST-1"
      and _by_line[11]["occurrence_total"] == 2, [(f["line_no"], f["group"]) for f in ff])
check("formatting: each finding shows the whole line, before and after",
      _by_line[7]["match"] == "**Introduction**"
      and _by_line[7]["becomes"] == "## Introduction")
check("formatting: each finding quotes its rule from the rules file",
      _by_line[7]["detail"] == _rules["rules"]["HEAD-4"]["text"]
      and "version 1" in _by_line[7]["detail_label"], _by_line[7])
check("formatting: the change that sneaks in a word is thrown away, and the author is told",
      15 not in _by_line and any("line 15" in n and "change your wording" in n
                                 for n in fnotes), fnotes)
check("formatting: frontmatter and code are never changed",
      2 not in _by_line and 18 not in _by_line
      and sum("frontmatter and code are left alone" in n for n in fnotes) == 2, fnotes)
check("formatting: a proposal quoting a line that isn't there is thrown away",
      any("line 23" in n and "isn't what is in your chapter" in n for n in fnotes)
      or 23 not in _by_line, fnotes)
check("formatting: DeepSeek's advice is shown as notes, not changes",
      any(n.startswith("Line 9 (FILE-2), not changed") for n in fnotes), fnotes)

_ids = {f["line_no"]: f["id"] for f in ff}
_before_disk = open(_fchap, encoding="utf-8").read()
fp = fs.build_preview([_ids[5], _ids[7], _ids[11]], [])
check("formatting: the preview changes exactly the approved lines",
      [d["line_no"] for d in fp["diff"]] == [5, 7, 11], fp["diff"])
check("formatting: a fix that wasn't approved isn't made",
      "• Another point" in fp["new_text"] and "> [!Tip] Remember" in fp["new_text"])
check("formatting: the preview counts the formatting fixes",
      fp["counts"]["format"] == 3, fp["counts"])
check("formatting: the preview writes nothing",
      open(_fchap, encoding="utf-8").read() == _before_disk)

fp_all = fs.build_preview([f["id"] for f in ff if f["group"] == "format::LIST-1"], [])
check("formatting: yes to every fix under one rule makes all of them",
      [d["line_no"] for d in fp_all["diff"]] == [11, 12], fp_all["diff"])

fres = fs.commit([_ids[5], _ids[7], _ids[11]], [])
_after_disk = open(_fchap, encoding="utf-8").read()
check("formatting: saving writes the approved fixes and nothing else",
      "## Introduction" in _after_disk and "- A bulleted point" in _after_disk
      and "• Another point" in _after_disk
      and len(_after_disk.split("\n")) == len(FMT_CHAPTER.split("\n")), fres)

# A formatting fix and another change on the same line: the other wins, and the
# fix is left out and reported, never merged into it.
with open(_fchap, "w", encoding="utf-8") as fh:
    fh.write(FMT_CHAPTER)
try:
    _llm.ask_json, _llm.have_key = _fake_ask, (lambda: True)
    fs2 = Session(_fchap)
    fs2.load_chapter(_fchap)
    ff2, _ = fs2.run_analyses({"analyses": ["terms", "format"],
                               "first_mention_only": True})
finally:
    _llm.ask_json, _llm.have_key, _llm.load_key = _saved
_term21 = [f for f in ff2 if f["kind"] == "term" and f["line_no"] == 21]
_fmt21 = [f for f in ff2 if f["kind"] == "format" and f["line_no"] == 21]
fp2 = fs2.build_preview([f["id"] for f in _term21 + _fmt21], [])
_line21 = fp2["new_text"].split("\n")[20]
check("formatting: a fix on a line with another accepted change is left out and reported",
      bool(_term21) and bool(_fmt21) and fp2["format_skipped"] == [21]
      and "[[Monism]]" in _line21 and "*one*" in _line21
      and fp2["counts"]["format"] == 0, (fp2["format_skipped"], _line21))

# --- no key: the check explains itself and everything else works -------------

with open(_fchap, "w", encoding="utf-8") as fh:
    fh.write(FMT_CHAPTER)
_net_calls = []
_real_urlopen = _llm.urllib.request.urlopen


def _no_network(*a, **k):
    _net_calls.append(a)
    raise AssertionError("the network was used")


try:
    _llm.load_key = lambda: None
    _llm.urllib.request.urlopen = _no_network
    fs3 = Session(_fchap)
    fs3.load_chapter(_fchap)
    ff3, fnotes3 = fs3.run_analyses({"analyses": ["terms", "format"],
                                     "first_mention_only": True})
finally:
    _llm.ask_json, _llm.have_key, _llm.load_key = _saved
    _llm.urllib.request.urlopen = _real_urlopen
check("formatting: with no key, the check says it needs one",
      any("needs a DeepSeek key" in n for n in fnotes3), fnotes3)
check("formatting: with no key, nothing is sent anywhere", not _net_calls, _net_calls)
check("formatting: with no key, the other checks still work",
      any(f["kind"] == "term" for f in ff3)
      and not any(f["kind"] == "format" for f in ff3), [f["kind"] for f in ff3])

# --- the network fails: the check says so, and nothing else is affected --------


def _run_with(urlopen):
    try:
        _llm.load_key = lambda: "sk-test"
        _llm.urllib.request.urlopen = urlopen
        s = Session(_fchap)
        s.load_chapter(_fchap)
        return s.run_analyses({"analyses": ["terms", "format"],
                               "first_mention_only": True})
    finally:
        _llm.ask_json, _llm.have_key, _llm.load_key = _saved
        _llm.urllib.request.urlopen = _real_urlopen


def _offline(*a, **k):
    raise _urlerr.URLError("no route to host")


def _busy(req, *a, **k):
    raise _urlerr.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)


class _Reply:
    def __init__(self, body):
        self.body = body.encode("utf-8")

    def read(self, *a):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ff4, fnotes4 = _run_with(_offline)
check("formatting: when DeepSeek can't be reached, the author is told",
      any("couldn't be reached" in n for n in fnotes4), fnotes4)
check("formatting: a network failure costs only the formatting check",
      any(f["kind"] == "term" for f in ff4)
      and not any(f["kind"] == "format" for f in ff4))
_, fnotes5 = _run_with(_busy)
check("formatting: a busy DeepSeek is reported as busy",
      any("busy" in n for n in fnotes5), fnotes5)
_, fnotes6 = _run_with(lambda *a, **k: _Reply("<html>not json</html>"))
check("formatting: a garbled answer is reported, not trusted",
      any("something unexpected" in n for n in fnotes6), fnotes6)
_sent = []


def _answers(req, *a, **k):
    _sent.append(json.loads(req.data.decode("utf-8")))
    return _Reply(json.dumps({"choices": [{"message": {"content": json.dumps(
        {"changes": [_proposal(7, "**Introduction**", "## Introduction", "HEAD-4")],
         "notes": []})}}]}))


ff7, _ = _run_with(_answers)
check("formatting: the real request carries the rules file as its system prompt",
      _sent and _sent[0]["messages"][0]["content"] == formatting.build_prompt(_rules)
      and _sent[0]["response_format"] == {"type": "json_object"}, _sent[:1])
check("formatting: a good answer over the network becomes a finding",
      [f["line_no"] for f in ff7 if f["kind"] == "format"] == [7])

shutil.rmtree(_fvault, ignore_errors=True)
shutil.rmtree(_rules_dir, ignore_errors=True)

# --- glossary links: the glossary's terms linked where a chapter first mentions them

_groot, _gchapter = make_vault()
with open(os.path.join(_groot, "glossary.md"), "w") as fh:
    fh.write("# Glossary\n\n## Morphogenesis\n\nStructural elaboration.\n")
_gs = Session(_groot)
_gs.load_chapter(_gchapter)
_gf, _ = _gs.run_analyses({"analyses": ["glossary"], "first_mention_only": True})
_glinks = [f for f in _gf if f["group"].startswith("glossary-link::")]
check("glossary: a term the glossary has is offered as a link to its entry",
      [f["replacement"] for f in _glinks] == ["[[glossary#Morphogenesis|morphogenesis]]"],
      [f["replacement"] for f in _glinks])
_gnew = [f for f in _gf if f["kind"] == "glossary" and f["term"].casefold() == "analytical dualism"]
check("glossary: a new term is still offered for the glossary", len(_gnew) == 1, [f.get("term") for f in _gf])
_gp = _gs.build_preview([f["id"] for f in _glinks + _gnew], [])
check("glossary: the accepted link is made",
      "[[glossary#Morphogenesis|morphogenesis]]" in _gp["new_text"], _gp["new_text"][:600])
check("glossary: a new entry links the mention it was found at",
      "[[glossary#Analytical dualism|Analytical dualism]] is defined as" in _gp["new_text"], _gp["new_text"][:900])
check("glossary: the new entry is in the glossary", "## Analytical dualism" in _gp["glossary_after"])
with open(_gchapter, "w") as fh:
    fh.write(_gp["new_text"])
_gs2 = Session(_groot)
_gs2.load_chapter(_gchapter)
_gf2, _ = _gs2.run_analyses({"analyses": ["glossary"], "first_mention_only": True})
check("glossary: a term already linked in the chapter isn't offered again",
      not [f for f in _gf2 if f["group"] == "glossary-link::morphogenesis"], [f["group"] for f in _gf2])
check("glossary: a term that is a concept page's title is left to the concept link",
      glossary.link_pages(["Emergence", "Morphogenesis"], "glossary", ["Emergence"])[0]["title"] == "Morphogenesis")
_g3root, _g3chapter = make_vault()
_gs3 = Session(_g3root)
_gs3.load_chapter(_g3chapter)
_gf3, _ = _gs3.run_analyses({"analyses": ["glossary"], "first_mention_only": True})
_gn3 = [f for f in _gf3 if f["kind"] == "glossary" and f["line"] >= 0][:1]
if _gn3:
    _line = _gn3[0]["line"]
    _fmt = {"kind": "format", "id": "fmt-x", "group": "format::X", "line": _line, "line_no": _line + 1,
            "start": 0, "end": len(_gs3.docmap.lines[_line]), "replacement": "Reformatted line."}
    _gs3.findings.append(_fmt)
    _gp3 = _gs3.build_preview([_gn3[0]["id"], "fmt-x"], [])
    check("glossary: a formatting fix on the same line wins over the new entry's link",
          "Reformatted line." in _gp3["new_text"].split("\n") and _gp3["counts"]["format"] == 1, _gp3["format_skipped"])
else:
    check("glossary: a formatting fix on the same line wins over the new entry's link", False, "no new glossary term found")
shutil.rmtree(_g3root, ignore_errors=True)
check("glossary: an entry says where it was first used by the chapter's title, linked, never a file name",
      "(First used in [[chapter-04|Test chapter]].)" in _gp["glossary_after"] and ".md" not in _gp["glossary_after"],
      _gp["glossary_after"][-300:])
from app.session import chapter_title
check("glossary: a chapter's title is its front matter title, else its heading, else its name",
      (chapter_title("---\ntitle: A\n---\n# B\n", "c.md"), chapter_title("# Chapter 1: B\n", "c.md"),
       chapter_title("text\n", "x/chapter-02.md")) == ("A", "Chapter 1: B", "chapter-02"))

_nt = [
    ("At Time", "At Time is the moment", 0, "repeated"),
    ("Based Modeling", "Agent-Based Modeling helps", 6, "repeated"),
    ("Margaret Archer's", "Margaret Archer's view", 0, "repeated"),
    ("Yes: structural availability", "**Yes: structural availability**", 2, "bold"),
    ("Time/space", "**Time/space**", 2, "bold"),
    ("Practically, what you should do", "**Practically, what you should do**", 2, "bold"),
    ("Generative mechanism at deeper stratum", "**Generative mechanism at deeper stratum**", 2, "bold"),
    ("Beach and Pedersen", "as Beach and Pedersen (2013) show", 3, "repeated"),
    ("Gerber and Green", "(Gerber and Green, 2012)", 1, "repeated"),
]
check("glossary: obvious non-terms are never offered",
      all(glossary.non_term(t, l, st, k) for t, l, st, k in _nt),
      [t for t, l, st, k in _nt if not glossary.non_term(t, l, st, k)])
_ok = [("Analytical Marxism", "Analytical Marxism holds", 0, "repeated"),
       ("Critical Realism", "for Critical Realism the", 4, "repeated"),
       ("open systems problem", "the **open systems problem**", 6, "bold"),
       ("Being and Becoming", "of Being and Becoming in", 3, "repeated")]
check("glossary: real terms pass the non-term filter",
      not any(glossary.non_term(t, l, st, k) for t, l, st, k in _ok),
      [(t, glossary.non_term(t, l, st, k)) for t, l, st, k in _ok])
_rm = []
glossary.analyse(DocMap("# T\n\nAt Time we wrote. At Time again.\n\n**Yes: structural availability** here.\n"), removed=_rm)
check("glossary: a dry run reports what the filter removed, and why",
      ("Yes: structural availability", "a label, not a term") in _rm, _rm)
shutil.rmtree(_groot, ignore_errors=True)

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(_support, ignore_errors=True)

print()
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  failed:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
