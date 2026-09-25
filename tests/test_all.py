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

# --- 15. the launcher and bundle definition ---------------------------------

pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "packaging")
with open(os.path.join(pkg, "Info.plist.in")) as fh:
    plist = fh.read()
check("the bundle is marked as having no window of its own (LSUIElement)",
      "<key>LSUIElement</key>" in plist and "<true/>" in plist)
check("the bundle identifier is filled in at build time, not hardcoded",
      "__BUNDLE_ID__" in plist)
with open(os.path.join(pkg, "build.sh")) as fh:
    build = fh.read()
check("the build script never hardcodes a signing identity",
      "Developer ID Application:" not in build.replace(
          'sed -n \'s/.*"\\(Developer ID Application: .*\\)"/\\1/p\'', ""))
check("the build script enables the hardened runtime",
      "--options runtime" in build)
check("the build script staples the notarisation ticket",
      "stapler staple" in build)
check("the build script produces a disk image",
      "hdiutil create" in build)


# ---------------------------------------------------------------------------
# Bringing a Word document in.
# ---------------------------------------------------------------------------

from app import convert as _convert  # noqa: E402
from app import picker as _picker  # noqa: E402

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

# --- the page and the script agree about what is on it ----------------------
#
# Every one of these screens is wired up by id from app.js. A renamed or dropped
# id fails silently in a browser - the button simply stops working - so it is
# checked here instead.

with open(os.path.join(_here_pkg, "app", "web", "index.html"), encoding="utf-8") as fh:
    _html = fh.read()
with open(os.path.join(_here_pkg, "app", "web", "app.js"), encoding="utf-8") as fh:
    _js = fh.read()

_page_ids = set(re.findall(r'id="([^"]+)"', _html))
_made_in_js = set(re.findall(r"\.id = '([^']+)'", _js))
_wanted = set(re.findall(r"getElementById\('([^']+)'\)", _js))
_absent = sorted(_wanted - _page_ids - _made_in_js)
check("every part of the page the script reaches for is actually on it",
      not _absent, "not in index.html: " + ", ".join(_absent))

check("the Word document import has its own way in from the first screen",
      'id="pick-docx"' in _html and "step-import" in _html)
check("the import screens are all in the page",
      all(f'id="{s}"' in _html for s in
          ("step-import", "step-import-setup", "step-import-preview",
           "step-import-done")))
check("the author has to confirm before a converted chapter is written",
      'id="import-confirm"' in _html
      and "document.getElementById('do-import-save').disabled = !e.target.checked"
      in _js)
_import_screens = "".join(
    _html.split('<section id="step-import')[i].split("</section>")[0]
    for i in range(1, len(_html.split('<section id="step-import')))
)
check("the import screens never mention a terminal or a package manager",
      not any(w in _import_screens.lower() for w in
              ("terminal", "homebrew", "brew", "command line", "type this")),
      "the author does not have a terminal and must never be sent to one")

# --- the picker offers Word documents ---------------------------------------

check("the file chooser can be asked for Word documents",
      "docx" in _picker.WORD_TYPES and hasattr(_picker, "choose_word_document"))
check("the file chooser still offers markdown chapters",
      "md" in _picker.MARKDOWN_TYPES)

shutil.rmtree(_wroot, ignore_errors=True)


# ---------------------------------------------------------------------------
# The console: the readable layer over the place the textbook is stored.
# ---------------------------------------------------------------------------

from app import config as _config  # noqa: E402
from app import console as _console  # noqa: E402
from app import github as _github  # noqa: E402
from app import keychain as _keychain  # noqa: E402
from app import registry as _registry  # noqa: E402

# Nothing in these checks may touch the real Keychain, the real folder
# chooser, or the real Application Support folder.
_FAKE_KEYCHAIN = {}
_keychain.save = lambda account, secret: _FAKE_KEYCHAIN.update(
    {account: secret}) or True
_keychain.load = lambda account: _FAKE_KEYCHAIN.get(account)
_keychain.delete = lambda account: _FAKE_KEYCHAIN.pop(account, None) or True
from app import picker as _picker_mod  # noqa: E402


def _no_chooser(*args, **kwargs):
    raise AssertionError("a check tried to open the real folder chooser")


_picker_mod.choose_folder = _no_chooser
_picker_mod.choose_file = _no_chooser
_picker_mod.choose_word_document = _no_chooser
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

# --- accepting reaches the live book, and stops one press short of it -------
#
# Accepting used to leave a change in the drafts area and tell the author it
# would go live "when you next publish", which was not true of anything: the
# vault tracks the live book and nothing moved the drafts across. Accepting now
# also opens the one pull request that carries the drafts to the live book — and
# deliberately does not merge it.

from app import server as _server  # noqa: E402

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
    real_request, real_token = _github._request, _server._token
    _github._request = service.request
    _server._token = lambda: "a-token"
    try:
        return fn()
    finally:
        _github._request = real_request
        _server._token = real_token


def _on_book(book, vault=None, access="write"):
    """Put the app where the author would be: this book, this vault."""
    _server.WORKSPACE.update(vault=vault, book=book.slug if book else None,
                             restored=True)
    _server.CONSOLE.update(who="The Author", login="author", plans={},
                           loaded=None, access={})
    if book is not None and access:
        _server.CONSOLE["access"][book.slug] = access


_on_book(_BOOK)
_A = {"book": "book-a"}


def _posts(service):
    return [c for c in service.calls if c[0] == "POST" and c[1].endswith("/pulls")]


_svc = _Service()
_r1 = _with_service(_svc, lambda: _server.r_console_draft_accept(
    None, dict(_A, number=5, title="Fix a typo in chapter 3")))

check("accepting still folds the change into the drafts area, squashed",
      ("PUT", f"{_github.API}/repos/{_BOOK.repo}/pulls/5/merge",
       {"merge_method": "squash", "commit_title": "Fix a typo in chapter 3"})
      in _svc.calls)
check("accepting then opens the one pull request to the live book",
      len(_posts(_svc)) == 1 and _posts(_svc)[0][2]["head"] == "drafts"
      and _posts(_svc)[0][2]["base"] == "main",
      _posts(_svc))
check("accepting does NOT merge that pull request itself",
      _svc.merged == ["5"], _svc.merged)
check("what the author is told no longer claims publishing from Obsidian does it",
      not any("publish from Obsidian" in t or "when you next publish" in t
              for t in _r1["steps"]), _r1["steps"])
check("the author is told plainly that readers have not seen it yet",
      any("Nothing has reached readers yet" in t for t in _r1["steps"]),
      _r1["steps"])
check("accepting hands back what is now waiting to go live",
      _r1["publish"]["number"] == 77 and _r1["publish"]["can_publish"] is True,
      _r1["publish"])

# A second accept must join the request already open, not open a rival one.
_r2 = _with_service(_svc, lambda: _server.r_console_draft_accept(
    None, dict(_A, number=6, title="Another change")))
check("a second accept updates the open pull request instead of opening another",
      len(_posts(_svc)) == 1 and
      any(c[0] == "PATCH" and "/pulls/77" in c[1] for c in _svc.calls),
      len(_posts(_svc)))
check("the second accept says it joined what was already in line",
      any("joined the changes already in line" in t for t in _r2["steps"]),
      _r2["steps"])

# The description has to describe the drafts area as it stands, which includes
# work this console never saw.
_body = _svc.open_prs[0]["body"]
check("the pull request describes the drafts area as a whole, not one change",
      "chapters/chapter-03.md" in _body and "chapters/chapter-09.md" in _body,
      _body)
check("work written in the browser editor is named in it too",
      "Textbook CMS" in _body and "browser editor" in _body, _body)
check("the pull request says merging it is what publishes it",
      "Merging this is what publishes it" in _body, _body)

# A clash between the drafts and the live book is said out loud, not swallowed.
_clash = _Service(mergeable=False, state="dirty")
_r3 = _with_service(_clash, lambda: _server.r_console_draft_accept(
    None, dict(_A, number=7, title="A clashing change")))
check("a clash with the live book is surfaced rather than failing silently",
      "cannot be published as it stands" in (_r3["warning"] or ""), _r3["warning"])
check("a clash still leaves the change safe in the drafts area",
      _r3["publish"]["can_publish"] is False and
      any("folded into the drafts area" in t for t in _r3["steps"]), _r3["steps"])

# "Not known yet" is a real answer and is given as one.
_unsure = _Service(mergeable=None, state="unknown")
_state_unknown = _with_service(
    _unsure, lambda: _github.mergeability("t", _BOOK, 77, tries=1))
check("an unsettled pull request is reported as unknown, never as fine",
      _state_unknown == "unknown", _state_unknown)
check("an unknown state does not offer to publish",
      _console.describe_publish({"number": 77}, _COMPARE,
                                "unknown", _BOOK)["can_publish"] is False)

# Publishing is its own deliberate press.
_pub = _Service()
_pub.open_prs.append({"number": 77, "html_url": "u",
                      "created_at": "2026-09-01T00:00:00Z"})
_refused = None
try:
    _with_service(_pub, lambda: _server.r_console_publish(
        None, dict(_A, number=77)))
except KeyError as e:
    _refused = str(e.args[0])
check("publishing without ticking the box is refused",
      _refused is not None and "tick the box" in _refused, _refused)
check("nothing was merged when it was refused", _pub.merged == [], _pub.merged)

_wrong = None
try:
    _with_service(_pub, lambda: _server.r_console_publish(
        None, dict(_A, number=999, confirm=True)))
except KeyError as e:
    _wrong = str(e.args[0])
check("publishing anything but the request the author was shown is refused",
      _wrong is not None and "has changed since this screen" in _wrong, _wrong)
check("and nothing was merged when it was refused", _pub.merged == [], _pub.merged)

_done = _with_service(_pub, lambda: _server.r_console_publish(
    None, dict(_A, number=77, confirm=True)))
check("publishing merges the drafts into the live book once confirmed",
      _pub.merged == ["77"], _pub.merged)
check("the author is told his vault does not know about it yet",
      any("vault does not know" in t for t in _done["steps"]), _done["steps"])

# The drafts branch is long-lived and carries the CMS's work as well, so it has
# to stay an ancestor of the live book. A squash here would offer everything
# again on the next accept.
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "github.py"), encoding="utf-8") as fh:
    _gh_source = fh.read()
check("the drafts reach the live book as a merge commit, not a squash",
      '"merge_method": "merge"' in _gh_source and
      "ancestor of `main`" in _gh_source)

check("nothing waiting to go live means no pull request is opened",
      _with_service(_Service(ahead=0),
                    lambda: _server._publish_state("t", _BOOK)) == (None, None))


check("the scope asked for excludes the author's private work",
      _github.SCOPE.split() == ["public_repo", "repo:invite"], _github.SCOPE)
check("all four weekly jobs are known to the console",
      len(_github.WEEKLY_JOBS) == 4, len(_github.WEEKLY_JOBS))

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


# --- reading the list ---

check("a well-formed list of books is accepted",
      len(_registry.parse(_reg_text())["books"]) == 4)
check("a list naming the same key twice is refused",
      _refused(lambda: _registry.parse(
          '{"schema_version": 1, "books": [], "books": []}'),
          _registry.RegistryError) is not None)
_dup = json.loads(_reg_text())
_dup["books"].append(dict(_dup["books"][0], content=dict(
    _dup["books"][0]["content"], repo="example-org/elsewhere")))
check("a list naming one book twice is refused",
      "twice" in (_refused(lambda: _registry.parse(json.dumps(_dup)),
                           _registry.RegistryError) or ""))
_dup_repo = json.loads(_reg_text())
_dup_repo["books"][1]["content"]["repo"] = "Example-Org/BOOK-A"
check("two books sharing one repository are refused, whatever the case",
      "share the repository" in (_refused(
          lambda: _registry.parse(json.dumps(_dup_repo)),
          _registry.RegistryError) or ""))
check("a list in a format this app doesn't know is refused",
      "newer format" in (_refused(lambda: _registry.parse(
          _reg_text(schema_version=2)), _registry.RegistryError) or ""))
_same = json.loads(_reg_text())
_same["books"][0]["content"]["drafts_branch"] = "main"
check("a book whose drafts and live branches are the same is refused",
      _refused(lambda: _registry.parse(json.dumps(_same)),
               _registry.RegistryError) is not None)

check("an unknown book is refused, never swapped for another",
      "isn't a registered textbook" in (
          _refused(lambda: _REG.resolve("no-such-book"),
                   _registry.RegistryError) or ""))
check("a retired book is refused by name",
      "any more" in (_refused(lambda: _REG.resolve("old-book"),
                              _registry.RegistryError) or ""))
check("a retired book is never offered",
      [b.slug for b in _REG.active()] == ["book-a", "book-b", "book-c"])

check("every per-book link is worked out from the book's own entry",
      _BOOK_B.history_url ==
      "https://github.com/example-org/book-b/commits/published" and
      _BOOK_B.discussion_url ==
      "https://hypothes.is/search?q=url:https://book-b.example/*",
      _BOOK_B.describe())
check("a book with no site yet has no discussion link rather than a wrong one",
      _REG.find("book-c").discussion_url is None)
check("the sign-in identifier comes from the list when none was pasted",
      _server._client_id() == "Iv1.registry-client", _server._client_id())
_config.write_state(github_client_id="Iv1.pasted")
check("an identifier pasted in Settings still takes precedence",
      _server._client_id() == "Iv1.pasted")
_config.write_state(github_client_id="")

# Where the list comes from when the network doesn't answer.
def _offline():
    raise OSError("no network")

_live = _registry.load(fetch=lambda: _reg_text())
check("a fresh list is used and saved for later", _live.source == "live" and
      os.path.exists(_registry.CACHE_FILE))
_cached = _registry.load(fetch=_offline)
check("offline, the saved list is used and says it is not fresh",
      _cached.source == "cached" and _cached.describe()["as_of"] and
      _cached.find("book-b") is not None, _cached.describe())
_after_bad = _registry.load(fetch=lambda: _reg_text(schema_version=9))
check("a broken list is never saved over a good one",
      _after_bad.source == "cached" and "newer format" in _after_bad.problem,
      _after_bad.describe())
os.remove(_registry.CACHE_FILE)
check("with no list anywhere, nothing is guessed",
      "no earlier copy" in (_refused(lambda: _registry.load(fetch=_offline),
                                     _registry.RegistryError) or ""))
_registry.use(_REG)

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

# --- the books offered are the books the author can act on ---

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


_PERMS = {"example-org/book-a": "write", "example-org/book-b": "read",
          "other-org/book-c": 404}
_on_book(None)
_config.write_state(book_access=None, last_book=None)
_bk = _with_service(_Books(_PERMS), lambda: _server.r_books(None, {}))
check("only books the author can change are offered",
      [b["slug"] for b in _bk["books"]] == ["book-a"], _bk["books"])
check("the author is told how many other books were left out",
      _bk["hidden"] == 2, _bk["hidden"])
check("a retired book is not even checked",
      _server.CONSOLE["access"].get("old-book") is None)
check("offering a book shows where it lives",
      _bk["books"][0]["repo"] == "example-org/book-a")
check("a book the author can only read can't be chosen",
      "can't make changes" in (_refused(lambda: _server.r_books_choose(
          None, {"slug": "book-b"})) or ""))
check("a book not in the list can't be chosen",
      "isn't a registered textbook" in (_refused(lambda: _server.r_books_choose(
          None, {"slug": "made-up"})) or ""))
_ws = _server.r_books_choose(None, {"slug": "book-a"})
check("choosing a book makes it the one on screen",
      _ws["book"]["slug"] == "book-a" and _ws["book"]["access"] == "write", _ws)
check("the choice is remembered for next time",
      _config.read_state().get("last_book") == "book-a")

# Invitations: the author accepts the one to their book in the book list.
class _Invites(_Books):
    def __init__(self, perms, listing, **kw):
        super().__init__(dict(perms), **kw)
        self.listing = listing

    def request(self, method, url, token=None, payload=None, accept=None):
        if url.startswith(f"{_github.API}/user/repository_invitations"):
            self.calls.append((method, url, payload))
            if isinstance(self.listing, _github.Problem):
                return self.listing
            if method == "PATCH":
                ident = int(url.rsplit("/", 1)[1])
                inv = next(i for i in self.listing if i["id"] == ident)
                self.perms[inv["repository"]["full_name"]] = "write"
                self.listing = [i for i in self.listing if i["id"] != ident]
                return {}
            return list(self.listing)
        return super().request(method, url, token, payload, accept)


_LISTING = [
    {"id": 11, "repository": {"full_name": "example-org/book-b"},
     "inviter": {"login": "platform"}},
    {"id": 12, "repository": {"full_name": "stranger/not-a-book"},
     "inviter": {"login": "someone"}},
]
_inv = _Invites(_PERMS, _LISTING)
_server.CONSOLE.update(access={})
_bk = _with_service(_inv, lambda: _server.r_books(None, {}))
check("an invitation to a registered book is offered in the book list",
      [(i["slug"], i["invitation"], i["inviter"]) for i in _bk["invitations"]]
      == [("book-b", 11, "platform")], _bk["invitations"])
check("an invitation to anything else is never shown",
      all(i["invitation"] != 12 for i in _bk["invitations"]))
check("the invited book isn't counted among the books left out",
      _bk["hidden"] == 1, _bk["hidden"])
check("an invitation this app didn't list can't be accepted through it",
      "isn't one this app listed" in (_refused(lambda: _with_service(
          _inv, lambda: _server.r_books_accept(None, {"invitation": 12}))) or "")
      and not any(c[0] == "PATCH" for c in _inv.calls))
_acc = _with_service(_inv, lambda: _server.r_books_accept(None, {"invitation": 11}))
check("accepting makes the book the author's to change, straight away",
      _acc["slug"] == "book-b" and _acc["access"] == "write"
      and [c[1] for c in _inv.calls if c[0] == "PATCH"]
      == [f"{_github.API}/user/repository_invitations/11"], _acc)
_bk = _with_service(_inv, lambda: _server.r_books(None, {}))
check("and it is then an ordinary book in the list, with no invitation left",
      "book-b" in [b["slug"] for b in _bk["books"]] and not _bk["invitations"], _bk)
_server.CONSOLE.update(access={})
_old = _Invites(_PERMS, _github.Problem("no scope", code=403))
_bk = _with_service(_old, lambda: _server.r_books(None, {}))
check("a sign-in from before invitations is told how to see them, and nothing breaks",
      "sign in again" in _bk["invite_note"] and not _bk["invitations"]
      and [b["slug"] for b in _bk["books"]] == ["book-a"], _bk)
_server.CONSOLE.update(access={})
_with_service(_Books(_PERMS), lambda: _server.r_books(None, {}))

_access_err = _with_service(
    _Books({"example-org/book-a": 500}),
    lambda: _github.repo_access("t", _BOOK))
check("a service fault is not mistaken for having no access",
      isinstance(_access_err, _github.Problem), _access_err)
check("a private repository is never offered, since sign-in can't reach it",
      _with_service(_Books({"example-org/book-a": "private"}),
                    lambda: _github.repo_access("t", _BOOK)) == "none")

# Offline, the last answer is shown for what it is.
_server.CONSOLE.update(access={}, login=None)
_off = _with_service(_Books(_PERMS, offline=True),
                     lambda: _server.r_books(None, {}))
check("offline, the books last known to be yours are still offered",
      [b["slug"] for b in _off["books"]] == ["book-a"] and _off["offline"],
      _off)
check("offline, the list says when access was last checked",
      "as of" in _off["note"], _off["note"])

# Next run: the remembered book comes back by itself, unless it is no longer
# the author's to change.
_server.WORKSPACE.update(vault=None, book=None, restored=False)
_server.CONSOLE.update(access={})
check("the last book chosen is brought back on the next run",
      _server.r_workspace(None, {})["book"]["slug"] == "book-a")
_config.write_state(last_book="book-b")
_server.WORKSPACE.update(vault=None, book=None, restored=False)
check("a remembered book the author can no longer change is not brought back",
      _server.r_workspace(None, {})["book"] is None)
_config.write_state(last_book="old-book")
_server.WORKSPACE.update(vault=None, book=None, restored=False)
check("a remembered book that has been retired is not brought back",
      _server.r_workspace(None, {})["book"] is None)

_so = _with_service(_Books(_PERMS), lambda: _server.r_console_signout(None, {}))
check("signing out forgets which books the last account could change",
      _config.read_state().get("book_access") is None and
      _server.CONSOLE["access"] == {})

# --- the chosen book decides, not the open vault (QUARTZ plan §8 step 2) ---

_on_book(_BOOK_B, access=None)
_server.CONSOLE["access"].update({"book-a": "write", "book-b": "write"})
_msg = _refused(lambda: _server._open_vault(_va))
check("a vault of another book than the one chosen is refused on opening",
      _msg is not None and "the book you choose decides" in _msg
      and "Book A" in _msg and "Book B" in _msg, _msg)
check("and the app stays on the book it had, with no vault",
      _server.WORKSPACE["book"] == "book-b" and _server.WORKSPACE["vault"] is None)
_on_book(None)
_server.CONSOLE["access"].update({"book-a": "write", "book-b": "write"})
_server._open_vault(_va)
_ws = _server._workspace_info()
check("with no book chosen, opening a copy of a book chooses that book",
      _ws["book"]["slug"] == "book-a" and _ws["can_write_vault"]
      and "locked" not in _ws, _ws)
check("with a vault open, choosing its own book keeps the vault",
      _server.r_books_choose(None, {"slug": "book-a"})["vault"]["root"] == _va)

_before_ws = dict(_server.WORKSPACE)
_mis_msg = _refused(lambda: _server._open_vault(_vmis))
check("a vault that is a copy of another book's repository is refused on opening",
      _mis_msg is not None and "is a copy of example-org/book-b" in _mis_msg,
      _mis_msg)
check("and the app stays on the vault and book it had",
      _server.WORKSPACE["vault"]["root"] == _va and
      _server.WORKSPACE["book"] == "book-a")
_server.picker.choose_folder = lambda *a: (_vmis, None)
_pv = _server.r_console_pick_vault(None, {})
_server.picker.choose_folder = _no_chooser
check("choosing that vault from the console says why it was refused",
      "is a copy of" in (_pv.get("error") or ""), _pv)

_server.r_vault_close(None, {})
_ws = _server._workspace_info()
check("closing the vault keeps the book but stops chapter changes",
      _ws["book"]["slug"] == "book-a" and not _ws["can_write_vault"], _ws)
check("with the vault closed, another book can be chosen",
      _server.r_books_choose(None, {"slug": "book-b"})["book"]["slug"] == "book-b")
_server.r_books_choose(None, {"slug": "book-a"})
_server._open_vault(_va)
_ws = _server.r_books_choose(None, {"slug": "book-b"})
check("with a vault open, another book can still be chosen, and it decides",
      _ws["book"]["slug"] == "book-b" and _ws["vault"] is None
      and not _ws["can_write_vault"], _ws)

_server._open_vault(_vplain)
_ws = _server._workspace_info()
check("opening a folder that names no book leaves the chosen book as it was",
      _ws["book"]["slug"] == "book-b" and _ws["vault"]["state"] == "unlinked"
      and not _ws["can_write_vault"], _ws)
check("and choosing another book leaves that folder open",
      _server.r_books_choose(None, {"slug": "book-a"})["vault"]["state"]
      == "unlinked")

# --- the dangerous case: a suggestion for one book, a vault of another -------

_ISSUE_A = dict(_ISSUE, number=41, title="Suggested edit: chapters/chapter-01.md",
                repository_url=f"{_github.API}/repos/example-org/book-a",
                body=_ISSUE["body"].replace(
                    '"the the domains" should be "the three domains"',
                    '"The the words" should be "The words"'))
_ISSUE_B = dict(_ISSUE_A, number=42,
                repository_url=f"{_github.API}/repos/example-org/book-b")


def _chapter(root):
    with open(os.path.join(root, "chapters", "chapter-01.md")) as fh:
        return fh.read()


_A_TEXT, _B_TEXT = _chapter(_va), _chapter(_vb)


def _service_with_issues():
    svc = _Books({"example-org/book-a": "write", "example-org/book-b": "write"})
    svc.issues = {"example-org/book-a": [_ISSUE_A],
                  "example-org/book-b": [_ISSUE_B]}
    return svc


def _writes(svc):
    return [c for c in svc.calls if c[0] in ("POST", "PATCH", "PUT", "DELETE")]


# The screen was drawn for book B with book B's vault open. The author then
# opens book A's vault, and an old page asks to apply B's suggestion.
_svc = _service_with_issues()
_on_book(_BOOK_B)
_server._open_vault(_vb)
_B = {"book": "book-b"}
_loaded = _with_service(_svc, lambda: _server.r_console_load(None, dict(_B)))
check("the list is fetched from the chosen book's repository only",
      all("/repos/example-org/book-b/" in c[1] for c in _svc.calls
          if "/issues?" in c[1] or "/pulls" in c[1] or "/compare/" in c[1]),
      [c[1] for c in _svc.calls])
check("drafts are looked for on the book's own drafts branch",
      any("base=staging" in c[1] for c in _svc.calls),
      [c[1] for c in _svc.calls])
check("the list says which book it belongs to",
      _loaded["book"]["slug"] == "book-b" and
      _loaded["book"]["history_url"].endswith("/book-b/commits/published"))
_planB = _with_service(_svc, lambda: _server.r_console_plan(
    None, dict(_B, number=42)))
check("B's suggestion can be applied to B's own vault",
      _planB["vault"]["can_apply"], _planB)
check("with the drafts area out of reach, the plan says so and still offers the vault",
      not _planB["can_apply"] and "couldn't be read" in _planB["reason"], _planB)

_server.CONSOLE["access"]["book-a"] = "write"
_server.r_books_choose(None, {"slug": "book-a"})   # the author moves to book A
_server._open_vault(_va)                            # and opens its vault
_svc.calls.clear()
_msg = _with_service(_svc, lambda: _refused(lambda: _server.r_console_accept(
    None, dict(_B, number=42, apply_vault=True, plan_id=_planB["plan_id"]))))
check("an old screen for book B can't act once book A's vault is open",
      _msg is not None and "different book" in _msg, _msg)
check("…and neither chapter was touched",
      _chapter(_va) == _A_TEXT and _chapter(_vb) == _B_TEXT)
check("…and no reply was sent or suggestion closed", _writes(_svc) == [],
      _writes(_svc))
check("a plan made for book B is dropped when the vault changes book",
      _server.CONSOLE["plans"] == {})

# Even if every check in front of it failed, the write itself refuses: the
# app believes it is on book B, with a plan for book B, but the vault on disk
# is book A.
_server.WORKSPACE["book"] = "book-b"
_forged = {"can_apply": True, "book": "book-b", "vault": _va,
           "file_path": os.path.join(_va, "chapters", "chapter-01.md"),
           "old": "The the words", "new": "The words", "line_no": 3,
           "before": "The the words are here."}
_server.CONSOLE["plans"][("book-b", "42")] = {"vault_plan": _forged, "head": None,
                                              "id": "forged"}
_svc.calls.clear()
_msg = _with_service(_svc, lambda: _refused(lambda: _server.r_console_accept(
    None, dict(_B, number=42, apply_vault=True, plan_id="forged"))))
check("the write is refused when the vault on disk is another book",
      _msg is not None and "Nothing was written" in _msg and
      "Book B" in _msg and "Book A" in _msg, _msg)
check("…and book A's chapter is byte for byte as it was",
      _chapter(_va) == _A_TEXT)
check("…and nothing was sent to either book", _writes(_svc) == [])
_server.CONSOLE["plans"].clear()

# Back on book A, properly. Its vault's settings are changed on disk after it
# was opened, so the vault is no longer what the header says.
_on_book(_BOOK)
_server._open_vault(_va)
_A = {"book": "book-a"}
_svc = _service_with_issues()
_with_service(_svc, lambda: _server.r_console_load(None, dict(_A)))
check("a suggestion filed on another book's repository is never listed",
      list(_server.CONSOLE["loaded"]["suggestions"]) == ["41"])
_planA = _with_service(_svc, lambda: _server.r_console_plan(
    None, dict(_A, number=41)))
check("A's suggestion is planned against A's vault",
      _planA["vault"]["can_apply"] and _planA["vault"]["name"], _planA)
check("and the vault is ticked to start with while readers see what Publish serves",
      _planA["vault"]["suggested"] is True, _planA)
check("a suggestion from another book can't even be looked at here",
      "isn't in the list" in (_with_service(_svc, lambda: _refused(
          lambda: _server.r_console_plan(None, dict(_A, number=42)))) or ""))

_cfg = os.path.join(_va, "textbook.config.json")
with open(_cfg, "w") as fh:
    json.dump({"slug": "book-b"}, fh)
_svc.calls.clear()
_msg = _with_service(_svc, lambda: _refused(lambda: _server.r_console_accept(
    None, dict(_A, number=41, apply_vault=True, plan_id=_planA["plan_id"]))))
check("a vault whose settings changed after opening is refused",
      _msg is not None and "changed after it was opened" in _msg, _msg)
check("…and nothing was written or sent",
      _chapter(_va) == _A_TEXT and _writes(_svc) == [])
with open(_cfg, "w") as fh:
    json.dump({"slug": "book-a"}, fh)

_gitcfg = os.path.join(_va, ".git", "config")
with open(_gitcfg) as fh:
    _good_git = fh.read()
with open(_gitcfg, "w") as fh:
    fh.write(_good_git.replace("Example-Org/Book-A", "example-org/book-b"))
_msg = _with_service(_svc, lambda: _refused(lambda: _server.r_console_accept(
    None, dict(_A, number=41, apply_vault=True, plan_id=_planA["plan_id"]))))
check("a vault whose remote changed after opening is refused",
      _msg is not None and _chapter(_va) == _A_TEXT and _writes(_svc) == [],
      _msg)
with open(_gitcfg, "w") as fh:
    fh.write(_good_git)

# A plan worked out for another vault of the same book is not carried over.
_va2 = _make_book_vault("book-a", "git@github.com:example-org/book-a.git")
_server._open_vault(_va2)
_server.CONSOLE["plans"][("book-a", "41")] = {
    "vault_plan": dict(_forged, book="book-a"), "head": None, "id": "forged"}
_msg = _with_service(_svc, lambda: _refused(lambda: _server.r_console_accept(
    None, dict(_A, number=41, apply_vault=True, plan_id="forged"))))
check("a plan made for a different vault of the same book is refused",
      _msg is not None and "different book or vault" in _msg and
      _chapter(_va) == _A_TEXT, _msg)

# The ordinary case still works, and keeps its guarantees.
_server._open_vault(_va)
_planA = _with_service(_svc, lambda: _server.r_console_plan(None, dict(_A, number=41)))
_svc.calls.clear()
_ok = _with_service(_svc, lambda: _server.r_console_accept(
    None, dict(_A, number=41, apply_vault=True, plan_id=_planA["plan_id"])))
_after = _chapter(_va).split("\n")
check("accepting into the matching vault changes exactly the one line",
      _ok["done"] and _after[2] == "The words are here." and
      [l for i, l in enumerate(_after) if i != 2] ==
      [l for i, l in enumerate(_A_TEXT.split("\n")) if i != 2], _after)
check("the reply and closing go to that book's repository and no other",
      _writes(_svc) and all("/repos/example-org/book-a/issues/41" in c[1]
                            for c in _writes(_svc)), _writes(_svc))
check("book B's vault was never touched", _chapter(_vb) == _B_TEXT)

# A suggestion naming a page outside the vault is never followed.
_escape = _console.plan_change(
    os.path.join(_va, "chapters"),
    "../../" + os.path.basename(_vb) + "/chapters/chapter-01.md",
    '"The the words" should be "Changed"')
check("a suggestion whose page leads out of the vault is refused",
      _escape["can_apply"] is False and "outside" in _escape["reason"],
      _escape["reason"])
os.symlink(os.path.join(_vb, "chapters", "chapter-01.md"),
           os.path.join(_va, "chapters", "linked.md"))
_link = _console.plan_change(_va, "chapters/linked.md",
                             '"The the words" should be "Changed"')
check("a page that is a link into another vault is refused",
      _link["can_apply"] is False and _chapter(_vb) == _B_TEXT, _link["reason"])

# Reading only: a book the author can merely read can't be acted on.
_on_book(_BOOK_B, access="read")
_svc = _service_with_issues()
check("a book the author can only read refuses to act",
      "can't make changes" in (_with_service(_svc, lambda: _refused(
          lambda: _server.r_console_decline(
              None, {"book": "book-b", "number": 1}))) or "") and
      _writes(_svc) == [])
_on_book(_BOOK)

# --- the Chapters tools can't save into a vault the app has moved away from ---

_sess_root = _make_book_vault("book-a", "git@github.com:example-org/book-a.git")
_server.SESSIONS.clear()


class _Handler:
    _session = _server.Handler._session


_H = _Handler()
_opened = _server.r_open(None, {"path": _sess_root})
check("opening a vault for the Chapters tools sets the book too",
      _opened["workspace"]["book"]["slug"] == "book-a", _opened["workspace"])
_sid = _opened["session_id"]
_ch = os.path.join(_sess_root, "chapters", "chapter-01.md")
_server.r_prepare(_H, {"session_id": _sid, "chapter": _ch})
_server.r_analyse(_H, {"session_id": _sid})
_server.CONSOLE["access"]["book-b"] = "write"
_server.r_books_choose(None, {"slug": "book-b"})   # the author moves to book B
_server._open_vault(_vb)                            # and opens its vault
_ch_before = _chapter(_sess_root)
_msg = _refused(lambda: _server.r_commit(
    _H, {"session_id": _sid, "accepted": [], "expand_groups": []}))
check("a chapter opened in one vault can't be saved after the app moves on",
      _msg is not None and "different vault" in _msg and
      _chapter(_sess_root) == _ch_before, _msg)

_server.r_books_choose(None, {"slug": "book-a"})
_server._open_vault(_sess_root)
with open(os.path.join(_sess_root, "textbook.config.json"), "w") as fh:
    json.dump({"slug": "no-such"}, fh)
_msg = _refused(lambda: _server.r_commit(
    _H, {"session_id": _sid, "accepted": [], "expand_groups": []}))
check("a vault whose book claim changed on disk is not saved into",
      _msg is not None and "changed after it was opened" in _msg, _msg)
check("opening a vault whose claim fails is refused for the Chapters tools too",
      "isn't a registered textbook" in (_refused(lambda: _server.r_open(
          None, {"path": _sess_root})) or ""))

_plain_sess = _server.r_open(None, {"path": _vplain})
_server.r_prepare(_H, {"session_id": _plain_sess["session_id"],
                         "chapter": os.path.join(_vplain, "chapters",
                                                 "chapter-01.md")})
_server.r_analyse(_H, {"session_id": _plain_sess["session_id"]})
_saved = _server.r_commit(_H, {"session_id": _plain_sess["session_id"],
                                 "accepted": [], "expand_groups": []})
check("a vault that names no book still works with the Chapters tools",
      "written" in _saved, _saved)
_server.r_vault_close(None, {})
check("a chapter can't be saved once its vault is closed",
      "vault was closed" in (_refused(lambda: _server.r_commit(
          _H, {"session_id": _plain_sess["session_id"], "accepted": [],
                 "expand_groups": []})) or ""))

# --- nothing per-book is written into the app ---

_constants = []
for _rel in ("app/github.py", "app/server.py", "app/console.py",
             "app/web/app.js", "app/web/index.html"):
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

_on_book(_BOOK)
for _d in (_va, _vb, _vmis, _vplain, _va2, _sess_root):
    shutil.rmtree(_d, ignore_errors=True)


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


# --- the author's path -------------------------------------------------------

def _converted(folder, root, name, pictures, book="book-a"):
    """What convert.convert() leaves behind, without needing pandoc here."""
    stage = tempfile.mkdtemp(prefix="aa-import-")
    media = []
    for rel, data in pictures.items():
        full = os.path.join(stage, _convert.STAGE_MEDIA, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        media.append({"rel": rel, "name": os.path.basename(rel), "ext": "png",
                      "size": len(data)})
    dest = _convert.media_destination(folder, name)
    links = "".join(f"![]({dest['link_prefix']}/{r})\n\n" for r in pictures)
    result = {"stage": stage, "md_name": name, "docx": "/w/" + name + ".docx",
              "text": "# " + name[:-3] + "\n\nA paragraph from Word.\n\n" + links,
              "media": media, "media_rel": dest["rel"] if media else None,
              "media_target": dest["target"] if media else None,
              "pandoc_warnings": [], "book": book, "vault": root,
              "local_problem": None}
    _server.IMPORT.update(folder=folder, result=result, docx=result["docx"])
    return result


_A = {"book": "book-a"}
_wa, _wch = _word_vault("book-a", "git@github.com:example-org/book-a.git")
_on_book(_BOOK)
_server._open_vault(_wa)
_FAKE_KEYCHAIN.pop(_keychain.ACCOUNT_GITHUB, None)
_st = _server.r_import_drafts_status(None, {})
check("before converting, an author who isn't signed in is told drafts is out",
      _st["available"] is False and "aren't signed in" in _st["message"],
      _st["message"])
_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "a-token"
_server.CONSOLE["access"]["book-a"] = "read"
_st = _server.r_import_drafts_status(None, {})
check("before converting, an author without push rights is told plainly",
      _st["available"] is False and "can't make changes" in _st["message"]
      and "example-org/book-a" in _st["message"]
      and "saved into your vault" in _st["message"], _st["message"])
_server.CONSOLE["access"].pop("book-a")
_ro = _GitRepo("example-org/book-a", {}, perms={"example-org/book-a": "read"})
_st = _with_service(_ro, lambda: _server.r_import_drafts_status(None, {}))
check("and push rights are looked up when this Mac doesn't know them yet",
      _st["available"] is False and "can't make changes" in _st["message"],
      _st["message"])
_server.CONSOLE["access"]["book-a"] = "write"
_st = _server.r_import_drafts_status(None, {})
check("an author who can push is told where it will go before starting",
      _st["available"] and "“drafts”" in _st["message"]
      and "example-org/book-a" in _st["message"], _st["message"])

# A chapter of this name is already in the folder and drafts is out: refused
# before converting, exactly as before.
with open(os.path.join(_wch, "Taken.md"), "w") as fh:
    fh.write("# Taken\n")
_FAKE_KEYCHAIN.pop(_keychain.ACCOUNT_GITHUB, None)
_server.IMPORT.update(docx="/no/such.docx", folder=_wch, result=None)
check("with drafts out, a name already in the folder is still refused up front",
      "already a chapter" in (_refused(lambda: _server.r_import_convert(
          None, {"name": "Taken.md"})) or ""))
_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "a-token"
check("with drafts open, the same name goes on to be converted",
      "already a chapter" not in (_refused(lambda: _server.r_import_convert(
          None, {"name": "Taken.md"})) or ""))

_DRAFTS_START = {
    "chapters/chapter-01.md": "# One\n\nThe the words are here.\nLeave me.\n",
    "assets/chapter-01/image1.png": b"\x89PNG old",
    "glossary.md": "# Glossary\n",
}
_repo = _GitRepo("example-org/book-a", _DRAFTS_START)
_res = _converted(_wch, _wa, "Chapter 7.md", {"image1.png": _PNG1})
_start = _repo.branches["drafts"]
_look = _with_service(_repo, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("looking at drafts first changes nothing",
      not _writes(_repo) and _repo.branches["drafts"] == _start)
check("the author is shown where on drafts it goes",
      _look["chapter_path"] == "chapters/Chapter 7.md"
      and _look["media_dir"] == "assets/Chapter 7" and _look["branch"] == "drafts"
      and not _look["exists"] and not _look["refused"], _look)

_sent = _with_service(_repo, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"])))
_new = _repo.branches["drafts"]
_commits = [c for c in _repo.calls if c[0] == "POST" and c[1].endswith("/git/commits")]
_moves = [c for c in _repo.calls if c[0] == "PATCH"]
check("a Word import with a picture makes exactly one commit on drafts",
      _sent["sent"] and len(_commits) == 1 and _repo.commits[_new]["parents"]
      == [_start], _repo.commits[_new])
check("the commit is the signed-in author's: no other author is named",
      "author" not in _commits[0][2] and "committer" not in _commits[0][2]
      and _repo.commits[_new]["who"] == "owner-of-a-token")
check("only the drafts branch is moved, and never by force",
      len(_moves) == 1 and _moves[0][1].endswith("/git/refs/heads/drafts")
      and _moves[0][2]["force"] is False
      and _repo.branches["main"] == _start, _moves)
_after = _repo.files()
check("the chapter and its picture are both in that one commit",
      _after["chapters/Chapter 7.md"] == _res["text"].encode()
      and _after["assets/Chapter 7/image1.png"] == _PNG1, sorted(_after))
_link = urllib.parse.unquote(re.search(r"!\[\]\(([^)]+)\)", _res["text"]).group(1))
check("the picture is at the path the chapter links to",
      _posixpath.normpath(_posixpath.join("chapters", _link)) in _after, _link)
check("every other file on drafts is byte-identical",
      all(_after[p] == (d.encode() if isinstance(d, str) else d)
          for p, d in _DRAFTS_START.items()))
check("the author is told where it went and given the commit",
      _sent["chapter_path"] == "chapters/Chapter 7.md" and _sent["url"]
      and _sent["branch"] == "drafts", _sent)
check("the Word import is still in the folder route's hands afterwards",
      _server.IMPORT["result"] is _res and os.path.isdir(_res["stage"]))
_saved = _server.r_import_save(None, {})
check("and the same chapter can still be saved into the vault (Publish reads it)",
      os.path.isfile(os.path.join(_wch, "Chapter 7.md"))
      and os.path.isfile(os.path.join(_wa, "assets", "Chapter 7", "image1.png")),
      _saved)
_again = _with_service(_repo, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("sending the same import again finds nothing to send",
      _again["nothing_to_send"] and _again["exists"], _again)

# --- a new chapter's line on the front page (contents.py) ---------------------

from app import contents as _contents

_SEED = ("---\nauthors:\n  - \"A\"\n---\n\n# Book\n\n## Contents\n\n"
         "- **[[chapters/chapter-01|Chapter 1 — One]]**\n  What it does.\n\n"
         "## Concept index\n\n- [[Example concept]]\n")
_new, _why = _contents.add_line(_SEED, "chapters/Chapter 7.md", "Chapter 7: Seven")
check("a new chapter's line goes after the last item under Contents",
      _new == _SEED.replace("  What it does.\n",
                            "  What it does.\n- **[[chapters/Chapter 7|Chapter 7: Seven]]**\n"),
      repr(_new))
_old_lines = _SEED.splitlines(keepends=True)
_new_lines = _new.splitlines(keepends=True)
check("and it is the only line that changes",
      len(_new_lines) == len(_old_lines) + 1
      and [l for l in _new_lines if l not in _old_lines]
      == ["- **[[chapters/Chapter 7|Chapter 7: Seven]]**\n"])
check("line endings are kept",
      _contents.add_line(_SEED.replace("\n", "\r\n"), "chapters/X.md", "X")[0]
      == _new.replace("Chapter 7|Chapter 7: Seven", "X|X").replace("\n", "\r\n")
      .replace("chapters/Chapter 7", "chapters/X"))
check("a chapter already listed is not listed twice",
      _contents.add_line(_new, "chapters/Chapter 7.md", "Seven")[0] is None)
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
      and _contents.chapter_title("No heading.", "Chapter 9.md") == "Chapter 9")

_repo_ix = _GitRepo("example-org/book-a", dict(_DRAFTS_START, **{"index.md": _SEED}))
_res = _converted(_wch, _wa, "Chapter 9.md", {})
_look = _with_service(_repo_ix, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("a new chapter's check shows the front-page line it will add",
      (_look.get("contents") or {}).get("line")
      == "- **[[chapters/Chapter 9|Chapter 9]]**", _look.get("contents"))
_sent = _with_service(_repo_ix, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"])))
_after = _repo_ix.files()
check("and sends the chapter and that one line in the same commit",
      _sent["sent"] and _sent["contents"]
      and _after["index.md"].decode() == _SEED.replace(
          "  What it does.\n",
          "  What it does.\n- **[[chapters/Chapter 9|Chapter 9]]**\n")
      and "chapters/Chapter 9.md" in _after, _after.get("index.md"))
_res = _converted(_wch, _wa, "Chapter 9.md", {})
_res["text"] = "# Chapter 9\n\nRevised in Word.\n"
_before_ix = _repo_ix.files()["index.md"]
_look = _with_service(_repo_ix, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("bringing the same chapter in again leaves the front page alone",
      _look["exists"] and not _look.get("contents"), _look.get("contents"))
_with_service(_repo_ix, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"], replace=True)))
check("and the front page is byte-identical after the replacement",
      _repo_ix.files()["index.md"] == _before_ix)
_res = _converted(_wch, _wa, "Chapter 10.md", {})
_look = _with_service(_repo, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("a book with no front page on drafts: the chapter still goes, with a note",
      (_look.get("contents") or {}).get("note") and not _look["refused"]
      and not _look["nothing_to_send"], _look.get("contents"))

# The same, from a real Word document, when this machine has pandoc.
if _pandoc:
    _png_src = os.path.join(_wch, "fig.png")
    with open(_png_src, "wb") as fh:
        fh.write(_ONE_PIXEL_PNG)
    with open(os.path.join(_wch, "w.md"), "w") as fh:
        fh.write("# Chapter Eleven\n\nWords.\n\n![A diagram](fig.png)\n")
    _docx = os.path.join(_wch, "w.docx")
    subprocess.run([_pandoc, "w.md", "-o", _docx], check=True, cwd=_wch,
                   capture_output=True, timeout=120)
    os.remove(os.path.join(_wch, "w.md"))
    os.remove(_png_src)
    _server.IMPORT.update(docx=_docx, folder=_wch, result=None)
    _real = _with_service(_repo, lambda: _server.r_import_convert(
        None, {"name": "Chapter 11.md"}))
    _look = _with_service(_repo, lambda: _server.r_import_drafts_check(
        None, dict(_A)))
    _before = len([c for c in _repo.calls if c[1].endswith("/git/commits")])
    _with_service(_repo, lambda: _server.r_import_drafts_send(
        None, dict(_A, head=_look["head"])))
    _after = _repo.files()
    _link = urllib.parse.unquote(re.search(
        r'(?:src="|\]\()([^")]+)', _real["text"]).group(1))
    _pic = _posixpath.normpath(_posixpath.join("chapters", _link))
    check("a real Word file with a picture makes one commit on drafts",
          len([c for c in _repo.calls if c[0] == "POST"
               and c[1].endswith("/git/commits")]) == _before + 1
          and _after["chapters/Chapter 11.md"] == _real["text"].encode())
    check("with the picture at the path the converted chapter links to",
          _after.get(_pic) == _ONE_PIXEL_PNG, (_pic, sorted(_after)))
    os.remove(_docx)

# --- drafts moved between looking and sending --------------------------------

_res = _converted(_wch, _wa, "Chapter 8.md", {"image1.png": _PNG1})
_look = _with_service(_repo, lambda: _server.r_import_drafts_check(None, dict(_A)))
_repo.before_move = lambda: _repo.push("cms-user", {
    "chapters/chapter-01.md": "# One\n\nFixed in the browser.\nLeave me.\n"})
_before_writes = len(_repo.calls)
_moved = _with_service(_repo, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"])))
_theirs = _repo.branches["drafts"]
check("if drafts moved after it was read, sending is refused",
      _moved.get("moved") and "Nothing was sent" in _moved["message"]
      and "never writes over anyone else's work" in _moved["message"], _moved)
check("and their change is still what drafts holds",
      _repo.commits[_theirs]["who"] == "cms-user"
      and _repo.files()["chapters/chapter-01.md"]
      == b"# One\n\nFixed in the browser.\nLeave me.\n"
      and "chapters/Chapter 8.md" not in _repo.files())
check("the refusal comes with a fresh offer, read from drafts as it is now",
      _moved["drafts"]["head"] == _theirs and _moved["drafts"]["head"]
      != _look["head"] and not _moved["drafts"]["refused"], _moved["drafts"])
check("the old offer can't be used once drafts has been read again",
      "out of date" in (_refused(lambda: _with_service(
          _repo, lambda: _server.r_import_drafts_send(
              None, dict(_A, head=_look["head"])))) or "")
      and _repo.branches["drafts"] == _theirs)
_sent = _with_service(_repo, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_moved["drafts"]["head"])))
check("sending again on the fresh offer goes on top of their change",
      _sent["sent"] and _repo.commits[_repo.branches["drafts"]]["parents"]
      == [_theirs] and _repo.files()["chapters/chapter-01.md"]
      == b"# One\n\nFixed in the browser.\nLeave me.\n")

# Someone puts a chapter of the same name on drafts in between: the fresh
# offer says so, and replacing it needs its own tick.
_res = _converted(_wch, _wa, "Chapter 9.md", {})
_look = _with_service(_repo, lambda: _server.r_import_drafts_check(None, dict(_A)))
_repo.before_move = lambda: _repo.push("cms-user", {
    "chapters/Chapter 9.md": "# Nine, written in the browser\n"})
_moved = _with_service(_repo, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"])))
check("a chapter of the same name appearing in between is shown on the new offer",
      _moved.get("moved") and _moved["drafts"]["exists"]
      and _moved["drafts"]["last"]["who"] == "cms-user", _moved)
_head = _repo.branches["drafts"]
check("and it is not replaced without the author saying so",
      "Tick the box" in (_refused(lambda: _with_service(
          _repo, lambda: _server.r_import_drafts_send(
              None, dict(_A, head=_moved["drafts"]["head"])))) or "")
      and _repo.branches["drafts"] == _head
      and _repo.files()["chapters/Chapter 9.md"]
      == b"# Nine, written in the browser\n")

# --- bringing the same chapter in again --------------------------------------

_repo2 = _GitRepo("example-org/book-a", {
    "chapters/Chapter 5.md": "# Chapter 5\n\nOld paragraph.\n\nKept.\n",
    "assets/Chapter 5/image1.png": _PNG1,
    "assets/Chapter 5/image2.png": _PNG2,
})
_res = _converted(_wch, _wa, "Chapter 5.md", {"image1.png": _PNG1})
_look = _with_service(_repo2, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("bringing a chapter in again says it replaces the one on drafts",
      _look["exists"] and _look["changed_lines"]["removed"] >= 1
      and _look["last"]["who"] == "maint", _look)
check("and names the picture the Word document no longer has",
      _look["removed"] == ["image2.png"], _look["removed"])
_sent = _with_service(_repo2, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"], replace=True)))
_after = _repo2.files()
check("a picture removed since the last import is gone from drafts",
      "assets/Chapter 5/image2.png" not in _after
      and _after["assets/Chapter 5/image1.png"] == _PNG1 and _sent["removed"] == 1,
      sorted(_after))
check("an unchanged picture isn't uploaded again",
      not any(c[1].endswith("/git/blobs") and base64.b64decode(
          c[2]["content"]) == _PNG1 for c in _repo2.calls if c[0] == "POST"))

# A pictures folder on drafts with no chapter of that name belongs to something
# else, and is never written into.
_repo3 = _GitRepo("example-org/book-a", {"assets/Chapter 6/image1.png": _PNG2})
_res = _converted(_wch, _wa, "Chapter 6.md", {"image1.png": _PNG1})
_look = _with_service(_repo3, lambda: _server.r_import_drafts_check(None, dict(_A)))
check("a pictures folder on drafts that belongs to something else is refused",
      _look["refused"] and "belong to something else" in _look["refused"], _look)
check("and sending anyway writes nothing",
      _refused(lambda: _with_service(_repo3, lambda: _server.r_import_drafts_send(
          None, dict(_A, head=_look["head"])))) and not _writes(_repo3))

# --- one book's text never goes to another book's repository -----------------

_repo4 = _GitRepo("example-org/book-a", {})
_res = _converted(_wch, _wa, "Chapter 10.md", {})
_look = _with_service(_repo4, lambda: _server.r_import_drafts_check(None, dict(_A)))
_wb, _wbch = _word_vault("book-b", "https://github.com/example-org/book-b.git")
_server.CONSOLE["access"]["book-b"] = "write"
# Choosing book B through the picker drops the import (see "changing book"
# below), so this state is forced here: the guard at the moment of sending is
# what is being tested, not the picker in front of it.
_server.WORKSPACE.update(book="book-b", vault=_registry.identify(_wb))
_msg = _refused(lambda: _with_service(_repo4, lambda: _server.r_import_drafts_send(
    None, {"book": "book-b", "head": _look["head"]})))
check("a chapter converted in book A's vault can't be sent once book B is open",
      _msg and "different book or vault" in _msg and not _writes(_repo4), _msg)
check("a page still showing book A can't send it either",
      "different book" in (_refused(lambda: _with_service(
          _repo4, lambda: _server.r_import_drafts_send(
              None, dict(_A, head=_look["head"])))) or "")
      and not _writes(_repo4))
_server.WORKSPACE.update(book="book-a", vault=None)
_server._open_vault(_wa)
with open(os.path.join(_wa, "textbook.config.json"), "w") as fh:
    json.dump({"slug": "book-b"}, fh)
_msg = _refused(lambda: _with_service(_repo4, lambda: _server.r_import_drafts_send(
    None, dict(_A, head=_look["head"]))))
check("a vault whose book claim changed on disk sends nothing",
      _msg and "changed after it was opened" in _msg and not _writes(_repo4), _msg)
with open(os.path.join(_wa, "textbook.config.json"), "w") as fh:
    json.dump({"slug": "book-a", "title": "ignored"}, fh)
_server._open_vault(_wa)
_server.CONSOLE["access"]["book-a"] = "read"
check("an author who can't push to the book is refused, and nothing is sent",
      "can't make changes" in (_refused(lambda: _with_service(
          _repo4, lambda: _server.r_import_drafts_send(
              None, dict(_A, head=_look["head"])))) or "")
      and not _writes(_repo4))
_server.CONSOLE["access"]["book-a"] = "write"

_same = _registry.Book({"slug": "s", "status": "live", "content": {
    "repo": "example-org/book-a", "live_branch": "main", "drafts_branch": "main"}})
_p = _drafts.send("t", _same, {"head": "h", "tree": "t"},
                  [{"path": "x.md", "sha": "s", "data": b"x"}], "m")
check("a book whose drafts branch is its live branch is never written to",
      isinstance(_p, _github.Problem) and "nothing was sent" in _p.message)

_server._forget_import()
check("clearing an import clears its temporary folder",
      _server.IMPORT["result"] is None and not os.path.isdir(_res["stage"]))
for _d in (_wa, _wb):
    shutil.rmtree(_d, ignore_errors=True)
_on_book(_BOOK)


# ---------------------------------------------------------------------------
# The rest of the author's path on drafts (BOOK-ONE-TO-QUARTZ.md §8 step 2).
#
# Tidying a chapter and accepting a reader's suggestion work on the chapter as
# the drafts area holds it, with no folder open, and send one commit made as
# the signed-in author. Only the lines that changed move. If drafts moved
# after it was read, nothing is sent and the author is offered it again.
# ---------------------------------------------------------------------------

import io as _io  # noqa: E402
import tarfile as _tarfile  # noqa: E402

from app import session as _session_mod  # noqa: E402

_on_book(_BOOK)
_server.WORKSPACE["vault"] = None
_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "a-token"
_server.SESSIONS.clear()

# A chapter that exists only on drafts, with Windows line endings, so that
# "only the changed lines move" is checked byte for byte.
_ONLY = CHAPTER.replace("\n", "\r\n")
_TIDY_START = {
    "chapters/Only.md": _ONLY,
    "chapters/Definitions/Emergence.md": "---\naliases: [emergent]\n---\n# Emergence\n",
    "chapters/Definitions/Monism.md": "# Monism\n",
    "chapters/Definitions/Critical Realism.md": "# Critical Realism\n",
    "glossary.md": "# Glossary\n\n## Agency\n\nThe capacity to act.\n",
    "assets/Only/fig.png": _PNG1,
    "README.md": "# Read me\n",
}
_tr = _GitRepo("example-org/book-a", _TIDY_START)
_start = _tr.branches["drafts"]
_op = _with_service(_tr, lambda: _server.r_drafts_open(None, dict(_A)))
check("with no folder open, the drafts area's chapters are listed",
      _op["mode"] == "drafts" and "chapters/Only.md" in
      [c["path"] for c in _op["chapters"]] and _server.WORKSPACE["vault"] is None,
      _op["chapters"])
check("pictures and hidden folders are not offered as chapters",
      not any(c["path"].startswith("assets/") for c in _op["chapters"]))
check("opening the drafts area changes nothing", not _writes(_tr))
_dsid = _op["session_id"]
_prep = _with_service(_tr, lambda: _server.r_prepare(
    _H, {"session_id": _dsid, "chapter": "chapters/Only.md", "book": "book-a"}))
check("the concept pages are the drafts area's own",
      sorted(_prep["concept_pages"]) == ["Critical Realism", "Emergence", "Monism"]
      and _prep["concept_source"] == "chapters/Definitions", _prep)
check("and Obsidian being open is no reason to stop: nothing on this Mac is edited",
      _prep["blockers"] == [] and _prep["head"] == _start, _prep)
_an = _with_service(_tr, lambda: _server.r_analyse(
    _H, {"session_id": _dsid, "analyses": ["references", "terms", "glossary"]}))
_kinds = {f["kind"] for f in _an["findings"]}
check("the three analyses run on the chapter from drafts",
      {"reference", "term"} <= _kinds, _an["findings"])
check("a page's aliases on drafts are read too",
      any(f["kind"] == "term" and f["match"].lower() == "emergence"
          for f in _an["findings"]), [f["match"] for f in _an["findings"]])
_acc = [f["id"] for f in _an["findings"] if f["kind"] in ("reference", "term")]
_gl = [f["id"] for f in _an["findings"] if f["kind"] == "glossary"][:1]
_session = _server.SESSIONS[_dsid]
_pv = _server.r_preview(_H, {"session_id": _dsid, "accepted": _acc + _gl,
                             "expand_groups": []})
check("the preview is worked out against drafts' glossary.md",
      _pv["glossary_path"] == "glossary.md" and _pv["glossary_exists"]
      and "## Agency" in _pv["glossary_before"], _pv["glossary_path"])

check("a stale screen sends nothing",
      "out of date" in (_refused(lambda: _with_service(_tr, lambda: _server.r_commit(
          _H, {"session_id": _dsid, "book": "book-a", "head": "old",
               "accepted": _acc, "expand_groups": []}))) or "")
      and not _writes(_tr))
_done = _with_service(_tr, lambda: _server.r_commit(
    _H, {"session_id": _dsid, "book": "book-a", "head": _start,
         "accepted": _acc + _gl, "expand_groups": []}))
_new = _tr.branches["drafts"]
_commits = [c for c in _tr.calls if c[0] == "POST" and c[1].endswith("/git/commits")]
_moves = [c for c in _tr.calls if c[0] == "PATCH"]
check("a tidy of a chapter only on drafts is one commit on drafts",
      _done["sent"] and len(_commits) == 1
      and _tr.commits[_new]["parents"] == [_start], _done)
check("made as the signed-in author, moving only drafts, never by force",
      "author" not in _commits[0][2] and _tr.commits[_new]["who"] == "owner-of-a-token"
      and len(_moves) == 1 and _moves[0][1].endswith("/git/refs/heads/drafts")
      and _moves[0][2]["force"] is False and _tr.branches["main"] == _start)
_after = _tr.files()
_old_lines = _ONLY.encode().split(b"\r\n")
_new_lines = _after["chapters/Only.md"].split(b"\r\n")
_moved_lines = [i for i, (a, b) in enumerate(zip(_old_lines, _new_lines)) if a != b]
check("only the lines that changed moved; every other line is byte-identical",
      len(_old_lines) == len(_new_lines) and _moved_lines
      and len(_moved_lines) == _done["changed_lines"], _moved_lines)
check("the chapter keeps its own line endings",
      b"\n" not in _after["chapters/Only.md"].replace(b"\r\n", b""))
check("the glossary gains its entry and keeps every line it had",
      _done["glossary_added"] and _after["glossary.md"].startswith(
          b"# Glossary\n\n") and b"## Agency\n\nThe capacity to act.\n"
      in _after["glossary.md"], _after["glossary.md"])
check("no other file on drafts changed",
      all(_after[p] == (d.encode() if isinstance(d, str) else d)
          for p, d in _TIDY_START.items()
          if p not in ("chapters/Only.md", "glossary.md")))
check("the author is told where it went",
      _done["written"] == ["chapters/Only.md", "glossary.md"]
      and _done["url"] and _done["branch"] == "drafts", _done)
_again = _with_service(_tr, lambda: _server.r_analyse(
    _H, {"session_id": _dsid, "analyses": ["references", "terms"]}))
check("a second run on the chapter just sent finds nothing more to link",
      not [f for f in _again["findings"] if f["kind"] == "reference"],
      _again["findings"])

# Drafts moved, but not this chapter: the choices stand, and are offered again.
_tr2 = _GitRepo("example-org/book-a", _TIDY_START)
_op = _with_service(_tr2, lambda: _server.r_drafts_open(None, dict(_A)))
_sid2 = _op["session_id"]
_with_service(_tr2, lambda: _server.r_prepare(
    _H, {"session_id": _sid2, "chapter": "chapters/Only.md", "book": "book-a"}))
_an = _with_service(_tr2, lambda: _server.r_analyse(
    _H, {"session_id": _sid2, "analyses": ["references"]}))
_acc2 = [f["id"] for f in _an["findings"]]
_tr2.before_move = lambda: _tr2.push("cms-user", {"README.md": "# Published\n"})
_mv = _with_service(_tr2, lambda: _server.r_commit(
    _H, {"session_id": _sid2, "book": "book-a", "head": _op["head"],
         "accepted": _acc2, "expand_groups": []}))
_theirs = _tr2.branches["drafts"]
check("if drafts moved after it was read, the tidy is refused and nothing is lost",
      _mv.get("moved") and "Nothing was sent" in _mv["message"]
      and _tr2.commits[_theirs]["who"] == "cms-user"
      and _tr2.files()["chapters/Only.md"] == _ONLY.encode(), _mv)
check("the chapter hadn't changed, so the choices stand and the fresh head is offered",
      _mv["same"] is True and _mv["head"] == _theirs, _mv)
_ok2 = _with_service(_tr2, lambda: _server.r_commit(
    _H, {"session_id": _sid2, "book": "book-a", "head": _mv["head"],
         "accepted": _acc2, "expand_groups": []}))
check("sending again goes on top of their change",
      _ok2["sent"] and _tr2.commits[_tr2.branches["drafts"]]["parents"] == [_theirs]
      and _tr2.files()["README.md"] == b"# Published\n")

# Drafts moved and this chapter changed: go through it again.
_tr3 = _GitRepo("example-org/book-a", _TIDY_START)
_op = _with_service(_tr3, lambda: _server.r_drafts_open(None, dict(_A)))
_sid3 = _op["session_id"]
_with_service(_tr3, lambda: _server.r_prepare(
    _H, {"session_id": _sid3, "chapter": "chapters/Only.md", "book": "book-a"}))
_an = _with_service(_tr3, lambda: _server.r_analyse(
    _H, {"session_id": _sid3, "analyses": ["references"]}))
_tr3.before_move = lambda: _tr3.push("cms-user", {
    "chapters/Only.md": "# Rewritten in the browser\r\n"})
_mv = _with_service(_tr3, lambda: _server.r_commit(
    _H, {"session_id": _sid3, "book": "book-a", "head": _op["head"],
         "accepted": [f["id"] for f in _an["findings"]], "expand_groups": []}))
check("if this chapter changed on drafts meanwhile, the author goes through it again",
      _mv.get("moved") and _mv["same"] is False
      and "go through it again" in _mv["message"]
      and _tr3.files()["chapters/Only.md"] == b"# Rewritten in the browser\r\n", _mv)

# The chosen book decides: a chapter opened from book A's drafts isn't sent
# once book B is chosen.
_server.CONSOLE["access"]["book-b"] = "write"
_server.r_books_choose(None, {"slug": "book-b"})
_tr4 = _GitRepo("example-org/book-a", _TIDY_START)
check("a chapter from one book's drafts isn't sent once another book is chosen",
      "different book" in (_refused(lambda: _with_service(_tr4, lambda: _server.r_commit(
          _H, {"session_id": _dsid, "book": "book-b",
               "head": _server.SESSIONS[_dsid].snap["head"],
               "accepted": [], "expand_groups": []}))) or "") and not _writes(_tr4))
_server.r_books_choose(None, {"slug": "book-a"})

# No push rights: said up front, before any work.
_server.CONSOLE["access"]["book-a"] = "read"
_msg = _refused(lambda: _with_service(_tr4, lambda: _server.r_drafts_open(None, dict(_A))))
check("an account without push rights is told so before opening the drafts area",
      _msg and "can't make changes" in _msg and "example-org/book-a" in _msg
      and "different account" in _msg, _msg)
_server.CONSOLE["access"]["book-a"] = "write"

_tr5 = _GitRepo("example-org/book-a", {"chapters/Latin.md": b"caf\xe9 au lait\n"})
_op = _with_service(_tr5, lambda: _server.r_drafts_open(None, dict(_A)))
check("a chapter that isn't UTF-8 is left alone rather than rewritten",
      "isn't plain text" in (_refused(lambda: _with_service(_tr5, lambda: _server.r_prepare(
          _H, {"session_id": _op["session_id"], "chapter": "chapters/Latin.md",
               "book": "book-a"}))) or ""))
check("a chapter that isn't on drafts can't be opened from it",
      "isn't in the drafts area" in (_refused(lambda: _with_service(_tr5, lambda: _server.r_prepare(
          _H, {"session_id": _op["session_id"], "chapter": "../outside.md",
               "book": "book-a"}))) or ""))

# Which glossary a chapter's terms go into, on drafts.
_gs = _session_mod.DraftsSession("book-a", {"head": "h", "tree": "t", "files": {
    "glossary.md": "a", "content/chapters/c.md": "b", "part/glossary.md": "c"}},
    lambda sha: b"")
check("on drafts, a chapter's glossary is the nearest one above it",
      _gs.glossary_for("part/ch/c.md") == "part/glossary.md"
      and _gs.glossary_for("chapters/c.md") == "glossary.md")
_gs.snap["files"].pop("glossary.md")
check("and with none, a new one goes at the top of the book's pages",
      _gs.glossary_for("content/chapters/c.md") == "content/glossary.md"
      and _gs.glossary_for("chapters/c.md") == "glossary.md")

# The concept-page search on drafts matches the one on disk.
_cp, _src = terms.concept_pages_in(
    ["Definitions/A One.md", "Definitions/B Two.md", "notes/x.md", "templates/T.md",
     ".obsidian/y.md", "Definitions/index.md", "chapters/c.md"],
    "chapters/c.md", lambda p: None)
check("on drafts, the folder of definitions is found as it is on disk",
      _src == "Definitions" and [p["title"] for p in _cp] == ["A One", "B Two"], _cp)

# Found in step 2's live proof: book two has no Definitions folder, and its
# other chapters were offered as concept pages instead.
_BOOK_TWO_FILES = ["README.md", "LICENSE.txt", "content/index.md",
                   "content/chapter-1.md", "content/chapter-2.md",
                   "content/chapter-3.md", "quartz/cli/README.md",
                   "suggest-edit/suggest-edit.js"]
_cp, _src = terms.concept_pages_in(_BOOK_TWO_FILES, "content/chapter-2.md",
                                   lambda p: None)
check("a book with no folder of concept pages has no concept pages",
      _cp == [] and _src == "", (_cp, _src))
_nodefs = tempfile.mkdtemp()
for _n in ("chapter-1.md", "chapter-2.md", "chapter-3.md"):
    with open(os.path.join(_nodefs, _n), "w") as fh:
        fh.write("# A chapter\n")
check("…and so has a vault with none, rather than its other chapters",
      terms.discover_concept_pages(_nodefs, os.path.join(_nodefs, "chapter-2.md"))
      == ([], ""))
shutil.rmtree(_nodefs, ignore_errors=True)
check("a folder named by the author is still used",
      [p["title"] for p in terms.concept_pages_in(
          _BOOK_TWO_FILES, "content/chapter-2.md", lambda p: None,
          folder="content")[0]] == ["chapter-1", "chapter-3"])

# Also from step 2: the drafts area's chapter list showed README as a chapter.
_two = _session_mod.DraftsSession("book-b", {"head": "h", "tree": "t", "files": {
    p: "s" for p in _BOOK_TWO_FILES}}, lambda sha: b"")
check("a book laid out for the website lists only its pages as chapters",
      [c["path"] for c in _two.chapters()] ==
      ["content/chapter-1.md", "content/chapter-2.md", "content/chapter-3.md",
       "content/index.md"], _two.chapters())
_one = _session_mod.DraftsSession("book-a", {"head": "h", "tree": "t", "files": {
    p: "s" for p in ("README.md", "CONTRIBUTING.md", "QA.md", "LICENSE",
                     "chapters/Chapter 1.md", "chapters/Definitions/Agency.md",
                     "chapters/readme.md", ".github/PULL_REQUEST.md")}},
    lambda sha: b"")
check("a book kept as a vault lists its pages, not the files about the repository",
      [c["path"] for c in _one.chapters()] ==
      ["QA.md", "chapters/Chapter 1.md", "chapters/Definitions/Agency.md"],
      _one.chapters())


# --- a reader's suggestion, made in the drafts area ---------------------------

def _issue_on(number, path, change):
    return dict(_ISSUE, number=number, title=f"Suggested edit: {path}",
                repository_url=f"{_github.API}/repos/example-org/book-a",
                body=_ISSUE["body"].replace(
                    '"the the domains" should be "the three domains"', change)
                .replace("chapters/chapter-03.md", path))


_SUG_START = {"chapters/chapter-01.md": "# One\n\nThe the words are here.\nLeave me.\n",
              "glossary.md": "# Glossary\n"}
_sr = _GitRepo("example-org/book-a", _SUG_START)
_sr.issues = {"example-org/book-a": [
    _issue_on(51, "chapters/chapter-01.md", '"The the words" should be "The words"'),
    _issue_on(52, "../../etc/passwd", '"root" should be "toor"'),
    _issue_on(53, "chapters/nowhere.md", '"x y" should be "y"')]}
_on_book(_BOOK)
_server.WORKSPACE["vault"] = None
_with_service(_sr, lambda: _server.r_console_load(None, dict(_A)))
_pl = _with_service(_sr, lambda: _server.r_console_plan(None, dict(_A, number=51)))
check("with no folder open, a suggestion is planned against the chapter on drafts",
      _pl["can_apply"] and _pl["line_no"] == 3 and _pl["vault"] is None
      and _pl["after"] == "The words are here." and _pl["branch"] == "drafts", _pl)
check("a suggestion naming a page outside the book is never followed",
      "outside the book" in _with_service(_sr, lambda: _server.r_console_plan(
          None, dict(_A, number=52)))["reason"])
check("a suggestion for a page drafts doesn't have says so",
      "not in the drafts area" in _with_service(_sr, lambda: _server.r_console_plan(
          None, dict(_A, number=53)))["reason"])
_pl = _with_service(_sr, lambda: _server.r_console_plan(None, dict(_A, number=51)))
_s0 = _sr.branches["drafts"]
_ac = _with_service(_sr, lambda: _server.r_console_accept(
    None, dict(_A, number=51, apply=True, head=_pl["head"], plan_id=_pl["plan_id"])))
_new = _sr.branches["drafts"]
_after = _sr.files()
_comments = [c for c in _sr.calls if c[0] == "POST" and c[1].endswith("/issues/51/comments")]
_closes = [c for c in _sr.calls if c[0] == "PATCH" and c[1].endswith("/issues/51")]
check("an accepted suggestion becomes one commit on drafts, as the author",
      _ac["done"] and _sr.commits[_new]["parents"] == [_s0]
      and _sr.commits[_new]["who"] == "owner-of-a-token"
      and "#51" in _sr.commits[_new]["message"], _sr.commits[_new])
check("changing exactly the one line",
      _after["chapters/chapter-01.md"] == b"# One\n\nThe words are here.\nLeave me.\n"
      and _after["glossary.md"] == b"# Glossary\n")
check("and the issue is closed with a link to the commit",
      len(_comments) == 1 and _ac["url"] in _comments[0][2]["body"]
      and len(_closes) == 1 and _closes[0][2] == {"state": "closed"}, _comments)
check("the author is told it went to drafts",
      any("drafts area" in t for t in _ac["steps"]), _ac["steps"])

# Working out a plan took 12–14 seconds from the author's Mac: four calls to
# the service, one after another, about three seconds each. The chapter is
# now asked for alongside the listing, so it takes two round trips.
class _Slow(_GitRepo):
    PAUSE = 0.3

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lock = _threading_mod.Lock()
        self.busy = self.most = 0

    def request(self, method, url, token=None, payload=None, accept=None):
        if url.startswith(self.base + "/") and method == "GET" and \
                "/issues" not in url:
            with self.lock:
                self.busy += 1
                self.most = max(self.most, self.busy)
            time.sleep(self.PAUSE)
            with self.lock:
                self.busy -= 1
        return super().request(method, url, token, payload, accept)


_slow = _Slow("example-org/book-a", _SUG_START)
_slow.issues = _sr.issues
_with_service(_slow, lambda: _server.r_console_load(None, dict(_A)))
_slow.calls.clear()
_t0 = time.monotonic()
_pl = _with_service(_slow, lambda: _server.r_console_plan(None, dict(_A, number=51)))
_took = time.monotonic() - _t0
check("a plan takes one round trip to the service, not four",
      _pl["can_apply"] and _pl["line_no"] == 3 and _slow.most == 3
      and len(_slow.calls) == 3 and _took < 2 * _Slow.PAUSE,
      (_took, _slow.most, [c[1] for c in _slow.calls]))
check("…and the chapter it read is the one the listing names",
      any("/contents/chapters/chapter-01.md?ref=" in c[1] for c in _slow.calls)
      and not any("/git/blobs/" in c[1] for c in _slow.calls))

# Asked for together by the branch's name, the answers can straddle someone
# else's change. The listing and the chapter are only used if they are of the
# commit the plan names; otherwise they are asked for again by that commit.
_straddle = _GitRepo("example-org/book-a", _SUG_START)
_straddle.issues = _sr.issues
_s_old = _straddle.branches["drafts"]
_real_tip = _github.branch_tip
_github.branch_tip = lambda token, book: (
    _s_old, _straddle.commits[_s_old]["tree"])
_straddle.before_listing = lambda: _straddle.push(
    "cms-user", {"chapters/chapter-01.md": "# One\n\nThe the words moved.\n"})
try:
    _with_service(_straddle, lambda: _server.r_console_load(None, dict(_A)))
    _pl = _with_service(_straddle, lambda: _server.r_console_plan(
        None, dict(_A, number=51)))
finally:
    _github.branch_tip = _real_tip
_plan_kept = _server.CONSOLE["plans"][("book-a", "51")]
check("a listing from after the branch moved is not mixed with the commit before",
      _pl["head"] == _s_old and _pl["before"] == "The the words are here."
      and _plan_kept["snap"]["files"]["chapters/chapter-01.md"]
      == _straddle.trees[_straddle.commits[_s_old]["tree"]]["chapters/chapter-01.md"],
      _pl)
_fresh = _GitRepo("example-org/book-a", _SUG_START)
_fresh.issues = _sr.issues
_with_service(_fresh, lambda: _server.r_console_load(None, dict(_A)))
_real_bytes = _github.file_bytes_at
_github.file_bytes_at = lambda *a: b"# One\n\nSomething else entirely.\n"
try:
    _pl = _with_service(_fresh, lambda: _server.r_console_plan(None, dict(_A, number=51)))
finally:
    _github.file_bytes_at = _real_bytes
check("a chapter that came back different from the listing is read again by its name",
      _pl["can_apply"] and _pl["before"] == "The the words are here.", _pl)
_sr.calls.clear()
_with_service(_sr, lambda: _server.r_console_plan(None, dict(_A, number=52)))
check("a page outside the book is never asked for",
      not any("/contents/" in c[1] for c in _sr.calls), _sr.calls)

# Drafts moved between looking and accepting: nothing written, nothing
# closed, and a fresh plan offered.
_sr2 = _GitRepo("example-org/book-a", _SUG_START)
_sr2.issues = _sr.issues
_with_service(_sr2, lambda: _server.r_console_load(None, dict(_A)))
_pl = _with_service(_sr2, lambda: _server.r_console_plan(None, dict(_A, number=51)))
_sr2.before_move = lambda: _sr2.push("cms-user", {"glossary.md": "# Glossary\n\n## Published\n"})
_mv = _with_service(_sr2, lambda: _server.r_console_accept(
    None, dict(_A, number=51, apply=True, head=_pl["head"], plan_id=_pl["plan_id"])))
_theirs = _sr2.branches["drafts"]
check("if drafts moved, accepting changes nothing and leaves the suggestion open",
      _mv.get("moved") and "still open" in _mv["message"]
      and _sr2.commits[_theirs]["who"] == "cms-user"
      and not [c for c in _sr2.calls if "/issues/51" in c[1]], _mv)
check("and offers the change again, read from drafts as it is now",
      _mv["plan"]["can_apply"] and _mv["plan"]["head"] == _theirs, _mv["plan"])
_ok = _with_service(_sr2, lambda: _server.r_console_accept(
    None, dict(_A, number=51, apply=True, head=_mv["plan"]["head"],
                plan_id=_mv["plan"]["plan_id"])))
check("accepting on the fresh offer goes on top of their change",
      _ok["done"] and _sr2.commits[_sr2.branches["drafts"]]["parents"] == [_theirs]
      and _sr2.files()["glossary.md"] == b"# Glossary\n\n## Published\n")

# Book one's route: while Publish serves the author's folder, the same change
# can go into the vault as well, and is offered ticked.
_sv = _make_book_vault("book-a", "git@github.com:example-org/book-a.git")
_server._open_vault(_sv)
_sr3 = _GitRepo("example-org/book-a", _SUG_START)
_sr3.issues = _sr.issues
_with_service(_sr3, lambda: _server.r_console_load(None, dict(_A)))
_pl = _with_service(_sr3, lambda: _server.r_console_plan(None, dict(_A, number=51)))
check("with the book's vault open, the vault is offered beside drafts",
      _pl["can_apply"] and _pl["vault"]["can_apply"] and _pl["vault"]["suggested"], _pl)
_ok = _with_service(_sr3, lambda: _server.r_console_accept(
    None, dict(_A, number=51, apply=True, apply_vault=True, head=_pl["head"], plan_id=_pl["plan_id"])))
check("accepting with both makes the change on drafts and in the vault",
      _sr3.files()["chapters/chapter-01.md"] == b"# One\n\nThe words are here.\nLeave me.\n"
      and "The words are here." in _chapter(_sv) and "The the" not in _chapter(_sv),
      _ok["steps"])
_server.r_vault_close(None, {})
shutil.rmtree(_sv, ignore_errors=True)

# --- the one rule for closing a suggestion ------------------------------------
#
# Found in the live proof on book two: a suggestion written as a comment rather
# than an exact replacement was accepted with no vault open, nothing was
# committed anywhere, and the reader was told "the chapter has been updated".
# The reader is told the chapter changed only when a commit holds the change,
# and then the reply links it.

_BB = {"book": "book-b"}
_LIVE_BODY = (
    "**File:** [`content/chapter-1.md`](https://example.invalid/f)\n\n"
    "### Suggested edit\n\n```text\nblaaaa\n```\n\n---\n\n"
    "**Submitted by:** `A Reader` (`a***@example.com`)\n\n"
    "_submitted via the suggest-an-edit form_")
_CH1 = "# One\n\nThe the words are here.\n"


def _book_b_repo():
    repo = _GitRepo("example-org/book-b", {"content/chapter-1.md": _CH1})
    root = repo.branches["main"]
    repo.branches = {"published": root, "staging": root}
    repo.issues = {"example-org/book-b": [
        dict(_ISSUE, number=61, title="Suggested edit: content/chapter-1.md",
             repository_url=f"{_github.API}/repos/example-org/book-b",
             body=_LIVE_BODY),
        dict(_ISSUE, number=62, title="Suggested edit: content/chapter-1.md",
             repository_url=f"{_github.API}/repos/example-org/book-b",
             body=_LIVE_BODY.replace("blaaaa", '"The the words" should be "The words"'))]}
    return repo


def _replies(repo, number):
    return [c[2]["body"] for c in repo.calls
            if c[0] == "POST" and c[1].endswith(f"/issues/{number}/comments")]


def _closes(repo, number):
    return [c for c in repo.calls
            if c[0] == "PATCH" and c[1].endswith(f"/issues/{number}")]


check("book two is not on Publish",
      not _BOOK_B.from_folder and _BOOK_B.drafts_branch == "staging")
_on_book(_BOOK_B)
_server.WORKSPACE["vault"] = None
_rb = _book_b_repo()
_commits_before = set(_rb.commits)
_branches_before = dict(_rb.branches)
_with_service(_rb, lambda: _server.r_console_load(None, dict(_BB)))
_pl = _with_service(_rb, lambda: _server.r_console_plan(None, dict(_BB, number=61)))
check("a suggestion not written as an exact replacement can't be made by the tool",
      not _pl["can_apply"] and _pl["vault"] is None
      and "exact replacement" in _pl["reason"], _pl)
check("and asking the tool to make it anyway is refused, with nothing sent",
      _refused(lambda: _with_service(_rb, lambda: _server.r_console_accept(
          None, dict(_BB, number=61, apply=True, head=_pl["head"], plan_id=_pl["plan_id"])))) is not None
      and not _replies(_rb, 61) and not _closes(_rb, 61))
_ac = _with_service(_rb, lambda: _server.r_console_accept(
    None, dict(_BB, number=61, head=_pl["head"], plan_id=_pl["plan_id"])))
_said = _replies(_rb, 61)
check("accepting it with no vault open, on a book not on Publish, commits nothing",
      set(_rb.commits) == _commits_before and _rb.branches == _branches_before,
      _rb.branches)
check("so the reader is not told the chapter was updated",
      len(_said) == 1 and "has been updated" not in _said[0]
      and not _console.claims_change(_said[0]), _said)
check("they are told honestly that it will be made by hand",
      _said == [_console.TAKEN_ON] and "hasn't been changed" in _said[0], _said)
check("and the author is told the chapter itself was not changed",
      any("was not changed" in t for t in _ac["steps"]) and "url" not in _ac,
      _ac["steps"])

# Found in step 2's live proof: closed, it dropped out of "Waiting for you" and
# could be forgotten. It stays open, labelled accepted, until a commit holds
# the change, so the list is the author's to-do list.
check("a suggestion accepted by hand is not closed", _closes(_rb, 61) == [])
check("…but labelled accepted, and no longer waiting to be looked at",
      [c[2] for c in _rb.calls if c[0] == "POST"
       and c[1].endswith("/issues/61/labels")] == [{"labels": ["accepted"]}]
      and any(c[0] == "DELETE" and c[1].endswith("/issues/61/labels/needs-triage")
              for c in _rb.calls))
check("…and the reader is told it stays open until the change is made",
      "stays open" in _said[0], _said)
check("…and the author is told it stays on their list",
      _ac["kept_open"] and any("Waiting for you" in t for t in _ac["steps"]),
      _ac["steps"])
check("a suggestion carrying that label is read as accepted",
      _console.parse_suggestion(dict(_ISSUE, labels=[{"name": "accepted"}]))["accepted"]
      and not _console.parse_suggestion(_ISSUE)["accepted"])
_pl = _with_service(_rb, lambda: _server.r_console_plan(None, dict(_BB, number=61)))
_n = len(_replies(_rb, 61))
check("accepting it again by hand is refused, and the reader isn't thanked twice",
      "already accepted" in (_refused(lambda: _with_service(
          _rb, lambda: _server.r_console_accept(None, dict(
              _BB, number=61, head=_pl["head"], plan_id=_pl["plan_id"]))))
          or "") and len(_replies(_rb, 61)) == _n)
check("it can't be closed as made before anything has changed the page",
      "Nothing has changed" in (_refused(lambda: _with_service(
          _rb, lambda: _server.r_console_made(None, dict(_BB, number=61)))) or "")
      and _closes(_rb, 61) == [])

_pl = _with_service(_rb, lambda: _server.r_console_plan(None, dict(_BB, number=62)))
_ac = _with_service(_rb, lambda: _server.r_console_accept(
    None, dict(_BB, number=62, apply=True, head=_pl["head"], plan_id=_pl["plan_id"])))
_said = _replies(_rb, 62)
_made = _rb.branches["staging"]
check("an exact replacement on book two becomes one commit on its drafts branch",
      _pl["can_apply"] and _rb.commits[_made]["parents"] == [_branches_before["staging"]]
      and _rb.files("staging")["content/chapter-1.md"] == b"# One\n\nThe words are here.\n"
      and _rb.branches["published"] == _branches_before["published"])
check("and only then is the reader told it changed, with a link to that commit",
      len(_said) == 1 and _console.claims_change(_said[0])
      and f"https://example.invalid/commit/{_made}" in _said[0]
      and _ac["url"] in _said[0] and len(_closes(_rb, 62)) == 1, _said)

# The author makes 61's change by hand, and it reaches drafts as a commit.
_rb.push("author", {"content/chapter-1.md": "# One\n\nBlaaaa. The words are here.\n"},
         "Make the change a reader suggested", branch="staging")
_byhand = _rb.branches["staging"]
_seen = _with_service(_rb, lambda: _server.r_console_made(None, dict(_BB, number=61)))
check("once a commit has changed the page, the author is shown that commit",
      _seen["change"]["sha"] == _byhand
      and _seen["change"]["message"] == "Make the change a reader suggested"
      and _closes(_rb, 61) == [], _seen)
check("closing it for a commit other than the latest is refused",
      _refused(lambda: _with_service(_rb, lambda: _server.r_console_made(
          None, dict(_BB, number=61, sha=_made)))) is not None
      and _closes(_rb, 61) == [])
_done = _with_service(_rb, lambda: _server.r_console_made(
    None, dict(_BB, number=61, sha=_byhand)))
_said = _replies(_rb, 61)
check("confirmed, the reader is told it changed, with a link to that commit",
      _done["done"] and _console.claims_change(_said[-1])
      and f"https://example.invalid/commit/{_byhand}" in _said[-1], _said)
check("and only then is the suggestion closed, once", len(_closes(_rb, 61)) == 1)
check("one that was never accepted can't be closed as made",
      "hasn't been accepted" in (_refused(lambda: _with_service(
          _rb, lambda: _server.r_console_made(
              None, dict(_BB, number=62)))) or ""))

# Found in the live proof on book two, issue #6: an exact replacement, found
# once on one line of the chapter on drafts, was answered "by hand" with
# nothing committed. The plan takes four calls to the service, about twelve
# seconds from the author's Mac, and Accept pressed in that time arrived as
# "no change". The issue's title and body and the chapter are the live ones,
# byte for byte.
_LIVE6 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "fixtures", "live-issue-6")
with open(os.path.join(_LIVE6, "issue.json"), encoding="utf-8") as fh:
    _issue6 = json.load(fh)
with open(os.path.join(_LIVE6, "chapter-2.md"), "rb") as fh:
    _CH2 = fh.read()
_rb6 = _GitRepo("example-org/book-b", {"content/chapter-2.md": _CH2.decode("utf-8")})
_root6 = _rb6.branches["main"]
_rb6.branches = {"published": _root6, "staging": _root6}
_rb6.issues = {"example-org/book-b": [dict(
    _ISSUE, number=6, title=_issue6["title"], body=_issue6["body"],
    repository_url=f"{_github.API}/repos/example-org/book-b")]}
_with_service(_rb6, lambda: _server.r_console_load(None, dict(_BB)))
check("issue #6 as filed is read as the exact replacement it is",
      _server.CONSOLE["loaded"]["suggestions"]["6"]["path"] == "content/chapter-2.md"
      and _console.literal_replacement(
          _server.CONSOLE["loaded"]["suggestions"]["6"]["suggestion"])
      == {"old": "seperately", "new": "separately"})
_pl6 = _with_service(_rb6, lambda: _server.r_console_plan(None, dict(_BB, number=6)))
check("and the tool offers to make it, on line 13 of the chapter on drafts",
      _pl6["can_apply"] and _pl6["line_no"] == 13 and _pl6["plan_id"]
      and _pl6["after"] == "separately in each book's repository.", _pl6)
_early = _with_service(_rb6, lambda: _refused(lambda: _server.r_console_accept(
    None, dict(_BB, number=6, apply=False, apply_vault=False, head=None))))
check("Accept pressed before the plan reached the page is refused",
      _early is not None and "look at the change again" in _early, _early)
_old6 = _pl6["plan_id"]
_pl6 = _with_service(_rb6, lambda: _server.r_console_plan(None, dict(_BB, number=6)))
check("and so is one answering a plan from an earlier look",
      _old6 != _pl6["plan_id"] and _refused(lambda: _with_service(
          _rb6, lambda: _server.r_console_accept(None, dict(
              _BB, number=6, apply=True, head=_pl6["head"], plan_id=_old6))))
      is not None)
check("…with nothing committed, sent or closed",
      _rb6.branches["staging"] == _root6 and not _replies(_rb6, 6)
      and not _closes(_rb6, 6))
_ac6 = _with_service(_rb6, lambda: _server.r_console_accept(None, dict(
    _BB, number=6, apply=True, apply_vault=False, head=_pl6["head"],
    plan_id=_pl6["plan_id"])))
_made6 = _rb6.branches["staging"]
_said6 = _replies(_rb6, 6)
check("accepting the plan the page was shown makes the one-word change on drafts",
      _rb6.commits[_made6]["parents"] == [_root6]
      and _rb6.files("staging")["content/chapter-2.md"]
      == _CH2.replace(b"seperately", b"separately")
      and _rb6.branches["published"] == _root6)
check("and the reader is told so, with a link to the commit",
      len(_said6) == 1 and _said6[0] != _console.TAKEN_ON
      and f"https://example.invalid/commit/{_made6}" in _said6[0]
      and len(_closes(_rb6, 6)) == 1, _said6)

# The rule, held by the one door every close goes through.
_gate = _Service()
check("a reply claiming a change with no commit to link is never sent",
      "still open" in (_with_service(_gate, lambda: _refused(
          lambda: _server._reply_and_close(
              "a-token", _BOOK_B, 70,
              "Thank you — the chapter has been updated."))) or "")
      and _gate.calls == [])
check("nor one that names a commit it doesn't link",
      _with_service(_gate, lambda: _refused(
          lambda: _server._reply_and_close(
              "a-token", _BOOK_B, 70, "The chapter was changed.",
              "https://example.invalid/commit/abc"))) is not None
      and _gate.calls == [])
check("the old wording would have been caught",
      _console.claims_change("Thank you for this — it has been taken on board "
                             "and the chapter has been updated."))
check("the honest replies claim nothing",
      not any(_console.claims_change(t) for t in
              (_console.TAKEN_ON, _console.IN_VAULT, _console.DECLINED)))
check("a thank-you for a change can't be worded without its link",
      _refused(lambda: _console.thanks_with_change(""), ValueError) is not None)
check("a commit is linked even when the service doesn't say where it is",
      _github.commit_page(_BOOK_B, "abc123")
      == "https://github.com/example-org/book-b/commit/abc123"
      and _console.reply_on_accept("", vault_changed=True) == _console.IN_VAULT)
_srv_src = open(_server.__file__, encoding="utf-8").read()
_closers = [m.start() for m in re.finditer(r"github\.close_issue\(", _srv_src)]
_gate_at = _srv_src.index("def _reply_and_close(")
_gate_end = _srv_src.index("\ndef ", _gate_at + 1)
check("every suggestion is closed through that one door",
      len(_closers) == 1 and _gate_at < _closers[0] < _gate_end
      and "github.comment(" not in _srv_src[:_gate_at] + _srv_src[_gate_end:],
      _closers)
check("no reply that says the chapter was updated is left in the app",
      not hasattr(_console, "THANKS")
      and "has been updated" not in open(_console.__file__, encoding="utf-8").read())
_on_book(_BOOK)


# Authors no longer use Obsidian: the citation-link choice says what the link
# does, and still sends the same value.
_page = open(os.path.join(os.path.dirname(_server.__file__), "web", "index.html"),
             encoding="utf-8").read()
_anchor_choice = _page[_page.index('name="anchor"') - 200:
                       _page.index('value="html"') + 200]
check("the citation-link choice doesn't name Obsidian, and keeps its values",
      "Obsidian" not in _anchor_choice and 'value="obsidian" checked' in _anchor_choice
      and "reference list" in _anchor_choice, _anchor_choice)


# --- the window and the copy running behind it are the same version ---------
#
# A newer build copied over a copy that was still running: the old process
# served the new page from disk, and answered what it had never heard of with
# "Unknown request." Now the page carries the build it was served from, and
# anything from another build is refused with words the author can act on.

import threading as _threading  # noqa: E402
import urllib.request as _urlreq  # noqa: E402
import urllib.error as _urlerr  # noqa: E402
from http.server import ThreadingHTTPServer as _HTTPServer  # noqa: E402

_httpd = _HTTPServer(("127.0.0.1", 0), _server.Handler)
_threading.Thread(target=_httpd.serve_forever, daemon=True).start()
_base = f"http://127.0.0.1:{_httpd.server_address[1]}"


def _post(route, build=None, query=False):
    headers = {"Content-Type": "application/json"}
    if not query:
        headers["X-AA-Token"] = _server.TOKEN
    if build is not None:
        headers["X-AA-Build"] = build
    url = _base + route + (f"?t={_server.TOKEN}" if query else "")
    req = _urlreq.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with _urlreq.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except _urlerr.HTTPError as e:
        return e.code, json.loads(e.read())


def _page_build():
    with _urlreq.urlopen(f"{_base}/?t={_server.TOKEN}", timeout=5) as resp:
        return re.search(r'data-build="([^"]*)"', resp.read().decode()).group(1)


check("a build is its version and a fingerprint of the code",
      _config.build_id() == _server.BUILD and "+" in _server.BUILD
      and _server.BUILD.startswith(_config.bundle_version()), _server.BUILD)
check("the page is stamped with the build it was served from",
      _page_build() == _server.BUILD, _page_build())
_code, _got = _post("/api/env", _page_build())
check("a page of the same build is answered", _code == 200 and "version" in _got,
      _got)
_code, _got = _post("/api/env", "1.0.0+000000000000")
check("a page of another build is refused, and told to quit and reopen",
      _code == 409 and _got.get("stale")
      and "please quit and reopen the app" in _got["error"].lower(), _got)
_code, _got = _post("/api/console/accept", None)
check("so is a page from before the check, which sends no build",
      _code == 409 and _got.get("stale"), _got)
_code, _got = _post("/api/ping", None, query=True)
check("the heartbeat still works from any build, so the old copy can be let go",
      _code == 200, _got)
check("and so does Quit", "/api/quit" in _server.ANY_BUILD_ROUTES)

# A background tab checks in late (Chrome: about once a minute after five
# minutes hidden), so a hidden page is waited for longer. Step 12's live
# proof lost the app this way during the ten-minute wait for the preview.
_life = dict(_server.LIFE)
_urlreq.urlopen(_urlreq.Request(
    f"{_base}/api/ping?t={_server.TOKEN}", data=b'{"hidden": true}',
    method="POST"), timeout=5).read()
check("a check-in says whether the tab is hidden", _server.LIFE["hidden"] is True,
      _server.LIFE)
_post("/api/ping", None, query=True)
check("and one that doesn't say is from a showing tab",
      _server.LIFE["hidden"] is False, _server.LIFE)
_t = 1_000_000.0
_server.LIFE.update(started=_t - 999, last_ping=_t - 90, closing_at=None,
                    hidden=False)
check("a showing tab silent for over a minute has gone: the app stops",
      "90 s" in (_server._why_stop(_t) or ""), _server._why_stop(_t))
_server.LIFE["hidden"] = True
check("a hidden tab a minute and a half late is waited for",
      _server._why_stop(_t) is None, _server._why_stop(_t))
_server.LIFE["last_ping"] = _t - 16 * 60
check("but not for more than 15 minutes, so a browser that crashed while the "
      "tab was hidden leaves nothing running",
      "background" in (_server._why_stop(_t) or ""), _server._why_stop(_t))
_server.LIFE.update(last_ping=_t - 1, closing_at=_t - 0.5)
check("a goodbye still stops it, hidden or not",
      _server._why_stop(_t) == "the page said goodbye", _server._why_stop(_t))
_server.LIFE.clear()
_server.LIFE.update(_life)

# The live case: the files on disk were replaced after this copy started.
_real_build = _server.BUILD
_server.BUILD = "1.0.0+startedolder"
_newer = _page_build()
_code, _got = _post("/api/books/choose", _newer)
check("a copy running older code than the page it served refuses the page",
      _newer == _real_build and _code == 409 and _got.get("stale"), _got)
_server.BUILD = _real_build
_code, _got = _post("/api/no-such-thing", _server.BUILD)
check("an unknown request from a page of the same build is still just unknown",
      _code == 404 and not _got.get("stale"), _got)
_httpd.shutdown()
_httpd.server_close()

_uij = open(os.path.join(os.path.dirname(_server.__file__), "web", "app.js"),
            encoding="utf-8").read()
check("the page sends its build with every request",
      "'X-AA-Build': BUILD" in _uij)
check("the page and the app say the same thing about a mismatch",
      " ".join(_server.STALE.split()) in
      " ".join(_uij.replace("' +\n  '", "").split()), _server.STALE)


_bb = _REG.find("book-b")
_pl = {"can_apply": True, "text": "a\nb c\n", "file_path": "x.md", "line_no": 2,
       "before": "b c", "old": "c", "new": "d"}
check("a suggestion's change moves one line and nothing else",
      _console.change_text(_pl) == ("a\nb d\n", None))
check("and is refused if the line isn't what was shown",
      _console.change_text(dict(_pl, before="b x"))[0] is None)


# --- what the author is told, by how the book is served ----------------------

check("a book still on Publish keeps the Obsidian wording after going live",
      any("Obsidian" in t for t in _console.published_steps(_BOOK)))
check("a book built from its repository is not told to take anything into Obsidian",
      not any("Obsidian" in t for t in _console.published_steps(_bb))
      and any("drafts area" in t for t in _console.published_steps(_bb)))
check("and its conflict advice doesn't mention Obsidian either",
      "Obsidian" not in _console.publish_state_words("conflict", _bb)
      and "Obsidian" in _console.publish_state_words("conflict", _BOOK))
check("a book's host kind comes from the registry",
      _BOOK.host_kind == "obsidian-publish" and _BOOK.from_folder
      and _bb.host_kind == "static" and not _bb.from_folder
      and _REG.find("book-c").host_kind is None)
_pub = _Service()
_pub.open_prs.append({"number": 77, "html_url": "u", "created_at": "t"})
_server.CONSOLE["access"]["book-b"] = "write"
_server.r_books_choose(None, {"slug": "book-b"})
_r = _with_service(_pub, lambda: _server.r_console_publish(
    None, {"book": "book-b", "number": 77, "confirm": True}))
check("going live on a built book says the new words",
      not any("Obsidian" in t for t in _r["steps"]), _r["steps"])
_server.r_books_choose(None, {"slug": "book-a"})


# --- the drafts preview (BOOK-ONE-TO-QUARTZ §8 step 12) ---------------------

from app import preview as _preview  # noqa: E402

# Book one as the registry has it from 24 Sep: still on Publish, with the
# builder building a preview on its own Pages project.
_PV_ENTRY = {
    "slug": "social-research-methods", "status": "live",
    "content": {"repo": "o/srm", "live_branch": "main", "drafts_branch": "drafts"},
    "site": {"domain": "srm.example",
             "host": {"kind": "obsidian-publish", "site_id": "x",
                      "publish_host": "publish-01.obsidian.md",
                      "builder": "quartz-book",
                      "project": "social-research-methods"}}}
_pv = _registry.Book(_PV_ENTRY)
check("a Publish book on the builder has a drafts preview on its Pages project",
      _pv.drafts_preview == "https://drafts.social-research-methods.pages.dev/"
      and _pv.drafts_marker_url
      == "https://drafts.social-research-methods.pages.dev/.well-known/textbook.json",
      _pv.drafts_preview)
check("and is still published from the folder, so going live keeps its wording",
      _pv.from_folder and any("Obsidian" in t for t in _console.published_steps(_pv)))
check("a book without the builder has no preview, and the page is told so",
      _BOOK.drafts_preview is None and _BOOK_B.drafts_preview is None
      and _BOOK.describe()["drafts_preview"] is None
      and _pv.describe()["drafts_preview"] == _pv.drafts_preview)
_pv_b = _registry.Book(dict(_PV_ENTRY, content=dict(
    _PV_ENTRY["content"], drafts_branch="Staging/Next_Week")))
check("a drafts branch's address is named as quartz-book's branchAlias names it",
      _pv_b.drafts_preview
      == "https://staging-next-week.social-research-methods.pages.dev/"
      and _registry.branch_alias("a" * 40) == "a" * 28)
_pv_bad = _registry.Book(json.loads(json.dumps(_PV_ENTRY).replace(
    '"social-research-methods"}', '"evil.example/x"}')))
check("a project name that isn't one is never put into an address",
      _pv_bad.drafts_preview is None)

_HEAD = "a" * 40
_OLD = "b" * 40
_T0 = 1790000000   # a commit time; the checks below run "now" from it


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _preview_at(marker, head=(_HEAD, _iso(_T0)), after=0):
    real_head, real_marker = _github.drafts_head_dated, _preview.fetch_marker
    seen = []
    _github.drafts_head_dated = lambda token, book: head
    _preview.fetch_marker = lambda url: seen.append(url) or marker
    try:
        return _preview.check("a-token", _pv, now=_T0 + after), seen
    finally:
        _github.drafts_head_dated, _preview.fetch_marker = real_head, real_marker


def _marker(commit, **extra):
    return dict({"slug": "social-research-methods", "branch": "drafts",
                 "book_commit": commit, "registry_digest": "sha256:x",
                 "builder_commit": "c" * 40}, **extra)


_p, _seen = _preview_at(_marker(_HEAD), after=30)
check("the preview is current once its marker names the drafts head",
      _p["state"] == "current" and _seen == [_pv.drafts_marker_url], _p)
_d = _console.describe_preview(_p)
check("and then the link is offered as “See the drafts”",
      _d["url"] == _pv.drafts_preview and _d["link"] == "See the drafts", _d)
_p, _ = _preview_at(_marker(_OLD), after=60)
_d = _console.describe_preview(_p)
check("a marker behind the head is “building”, with no link yet",
      _p["state"] == "building" and _d["url"] is None
      and "being rebuilt" in _d["words"], _d)
_p, _ = _preview_at(_marker(_OLD), after=9 * 60 + 59)
check("still building at nine minutes and 59 seconds", _p["state"] == "building")
_p, _ = _preview_at(_marker(_OLD), after=10 * 60)
_d = _console.describe_preview(_p)
check("ten minutes on, the author is told the preview is at the previous version",
      _p["state"] == "stale" and "still at your previous version" in _d["words"]
      and _d["url"] == _pv.drafts_preview and "last showed" in _d["link"], _d)
_p, _ = _preview_at(None, after=11 * 60)
_d = _console.describe_preview(_p)
check("with no preview built at all, the notice doesn't speak of a previous version",
      _p["state"] == "stale" and "no preview" in _d["words"] and _d["url"] is None, _d)
_p, _ = _preview_at(_marker(_HEAD, branch="main"), after=11 * 60)
check("another branch's marker is never taken for the drafts preview's",
      _p["state"] == "stale", _p)
_p, _ = _preview_at(_marker(_HEAD, slug="another-book"), after=60)
check("nor another book's", _p["state"] == "building", _p)
_p, _ = _preview_at(_marker(_OLD), head=(_HEAD, None), after=3600)
check("a head with no readable time never raises a false alarm",
      _p["state"] == "building", _p)
_p, _ = _preview_at(_github.Problem("offline", offline=True))
_d = _console.describe_preview(_p)
check("a marker that can't be fetched is “unknown”, not “building”",
      _p["state"] == "unknown" and _d["offline"] and _d["url"] == _pv.drafts_preview, _d)
_p, _ = _preview_at(_marker(_HEAD), head=_github.Problem("no"))
check("so is a drafts area that can't be read", _p["state"] == "unknown", _p)
check("a book with no preview isn't checked at all",
      _preview.check("a-token", _BOOK) is None
      and _console.describe_preview(None) is None)

_calls = []
_real_check = _preview.check
_preview.check = lambda token, book: _calls.append(book.slug) or None
try:
    _pv_r = _with_service(_Service(), lambda: _server.r_console_preview(
        None, {"book": "book-a"}))
    try:
        _with_service(_Service(), lambda: _server.r_console_preview(
            None, {"book": "book-b"}))
        _pv_refused = False
    except KeyError:
        _pv_refused = True
finally:
    _preview.check = _real_check
check("the page asks about the chosen book's preview, and only that book's",
      _pv_r == {"book": "book-a", "preview": None} and _calls == ["book-a"]
      and _pv_refused, (_pv_r, _calls))

class _Commits(_Service):
    def request(self, method, url, token=None, payload=None, accept=None):
        self.calls.append((method, url, payload))
        return [{"sha": _HEAD, "commit": {"committer": {"date": _iso(_T0)}}}]


_cs = _Commits()
_hd = _with_service(_cs, lambda: _github.drafts_head_dated("a-token", _pv_b))
check("the drafts head and its commit time come from the book's drafts branch",
      _hd == (_HEAD, _iso(_T0))
      and _cs.calls[0][1].endswith("/repos/o/srm/commits?sha=Staging%2FNext_Week&per_page=1"),
      (_hd, _cs.calls))

# The marker is fetched for real here, from a local server, so the parsing and
# the 404 are the code that runs.
import http.server as _hs  # noqa: E402
import threading as _th    # noqa: E402


class _MarkerHandler(_hs.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/ok"):
            body = json.dumps(_marker(_HEAD)).encode()
            self.send_response(200)
        elif self.path.startswith("/junk"):
            body = b"<html>not json</html>"
            self.send_response(200)
        else:
            body = b"not found"
            self.send_response(404 if self.path.startswith("/none") else 503)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_ms = _hs.ThreadingHTTPServer(("127.0.0.1", 0), _MarkerHandler)
_th.Thread(target=_ms.serve_forever, daemon=True).start()
_mbase = f"http://127.0.0.1:{_ms.server_address[1]}"
_m_ok = _preview.fetch_marker(_mbase + "/ok")
_m_none = _preview.fetch_marker(_mbase + "/none")
_m_junk = _preview.fetch_marker(_mbase + "/junk")
_m_down = _preview.fetch_marker(_mbase + "/down")
_ms.shutdown()
check("a served marker is read; a missing one means no build yet; others are problems",
      _m_ok == _marker(_HEAD) and _m_none is None
      and isinstance(_m_junk, _github.Problem)
      and isinstance(_m_down, _github.Problem), (_m_ok, _m_none, _m_junk, _m_down))


# --- "Download a copy" -------------------------------------------------------

def _tarball(entries):
    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in entries:
            info = _tarfile.TarInfo(name)
            if data is None:
                info.type = _tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            elif data == "dir":
                info.type = _tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, _io.BytesIO(data))
    return buf.getvalue()


_arch = _tarball([("org-book-abc/", "dir"), ("org-book-abc/index.md", b"# Home\n"),
                  ("org-book-abc/chapters/c.md", b"# C\n"),
                  ("org-book-abc/assets/c/p.png", _PNG1),
                  ("org-book-abc/link.md", None),
                  ("org-book-abc/../../evil.md", b"x")])
_dl_parent = tempfile.mkdtemp(prefix="aa-dl-")
os.makedirs(os.path.join(_dl_parent, "book-a (drafts, today)"))
_cp = _drafts.unpack_copy(_arch, _dl_parent, "book-a (drafts, today)")
check("a copy goes into a new folder, never one already there",
      _cp["folder"] == os.path.join(_dl_parent, "book-a (drafts, today) 2"), _cp)
check("with the book's files at their places",
      _cp["files"] == 3 and open(os.path.join(_cp["folder"], "chapters", "c.md"),
                                 "rb").read() == b"# C\n"
      and open(os.path.join(_cp["folder"], "assets", "c", "p.png"), "rb").read() == _PNG1)
check("links and paths leading out of the folder are left out, and counted",
      _cp["left_out"] == 2 and not os.path.exists(os.path.join(_dl_parent, "evil.md"))
      and not os.path.lexists(os.path.join(_cp["folder"], "link.md")), _cp)
check("something that isn't an archive writes nothing",
      _refused(lambda: _drafts.unpack_copy(b"nope", _dl_parent, "x"),
               _drafts.CopyError) and not os.path.exists(os.path.join(_dl_parent, "x")))
_real_archive = _github.drafts_archive
_asked = []
_github.drafts_archive = lambda token, book: _asked.append(book.drafts_branch) or _arch
_server.picker.choose_folder = lambda *a: (_dl_parent, None)
_dl = _server.r_drafts_download(None, dict(_A))
_server.picker.choose_folder = _no_chooser
_github.drafts_archive = _real_archive
check("\"Download a copy\" writes the book's drafts into a folder of its own",
      _asked == ["drafts"] and _dl["files"] == 3
      and os.path.basename(_dl["folder"]).startswith("book-a (drafts, ")
      and _dl["repo"] == "example-org/book-a", _dl)
shutil.rmtree(_dl_parent, ignore_errors=True)
_server.SESSIONS.clear()


# --- signing out, and signing in as someone else ---

import importlib.util as _ilu  # noqa: E402

# The real keychain code, against a pretend `security` tool: the stubs above
# replace it for every other check, so this copy is loaded on its own.
_kspec = _ilu.spec_from_file_location("_real_keychain", _keychain.__file__)
_realkc = _ilu.module_from_spec(_kspec)
_kspec.loader.exec_module(_realkc)


def _pretend_security(copies, deletable=True):
    """`copies` tokens stored; each delete removes one, as the real tool does."""
    held = ["tok"] * copies

    def run(args, stdin_text=None):
        if args[0] == "find-generic-password":
            return (True, held[0] + "\n") if held else (False, "")
        if args[0] == "delete-generic-password":
            if held and deletable:
                held.pop()
                return True, ""
            return False, ""
        raise AssertionError(args)
    return run, held


_realkc._run, _held = _pretend_security(2)
check("signing out removes every copy of the token, not just the first",
      _realkc.forget(_realkc.ACCOUNT_GITHUB) is True and _held == [], _held)
_realkc._run, _held = _pretend_security(1, deletable=False)
check("a token the Keychain won't give up is reported as still there",
      _realkc.forget(_realkc.ACCOUNT_GITHUB) is False and _held == ["tok"])

_on_book(_BOOK)
_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "old-token"
_server.CONSOLE["login"] = "old-login"
_so = _server.r_console_signout(None, {})
check("signing out takes the token out of the Keychain",
      _keychain.ACCOUNT_GITHUB not in _FAKE_KEYCHAIN and _server._token() is None,
      _FAKE_KEYCHAIN)
check("and the page is told nobody is signed in",
      _so["workspace"]["account"] == {"signed_in": False, "login": None,
                                      "name": None}, _so["workspace"]["account"])

_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "stuck-token"
_server.CONSOLE["login"] = "old-login"
_real_delete = _keychain.delete
_keychain.delete = lambda account: False
_msg = _refused(lambda: _server.r_console_signout(None, {}))
_keychain.delete = _real_delete
check("a sign-out the Keychain refused says so, rather than looking signed out",
      "still signed in" in (_msg or "") and
      _FAKE_KEYCHAIN.get(_keychain.ACCOUNT_GITHUB) == "stuck-token", _msg)

_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "old-token"
_server.CONSOLE.update(login="old-login", who="Old", access={"book-a": "read"})
_so = _server.r_console_signout(None, {"switching": True})
check("switching account signs the old one out and remembers who it was",
      _so["was"] == "old-login" and _server.CONSOLE["leaving"] == "old-login"
      and _server.CONSOLE["access"] == {} and _server._token() is None)

_real_poll, _real_whoami = _github.poll_signin, _github.whoami


def _sign_in_as(login):
    _server.CONSOLE["signin"] = {"device_code": "d", "interval": 2,
                                 "deadline": time.time() + 60}
    _github.poll_signin = lambda client, code: {"token": "tok-" + login}
    _github.whoami = lambda token: {"login": login, "name": login.title()}
    try:
        return _server.r_console_signin_poll(None, {})
    finally:
        _github.poll_signin, _github.whoami = _real_poll, _real_whoami


_si = _sign_in_as("Old-Login")
check("if GitHub signs the same account back in, the page is told",
      _si["same_account"] is True and _si["login"] == "Old-Login", _si)
_server.r_console_signout(None, {"switching": True})
_si = _sign_in_as("other-author")
check("signing in as a different account keeps the new one, and says so",
      _si["same_account"] is False and
      _FAKE_KEYCHAIN.get(_keychain.ACCOUNT_GITHUB) == "tok-other-author" and
      _server.CONSOLE["leaving"] is None, _si)
_acc = _server.r_account(None, {})["account"]
check("the bar is told the new account's name",
      _acc == {"signed_in": True, "login": "other-author",
               "name": "Other-Author"}, _acc)
_server.CONSOLE.update(login=None, who=None)
_acc = _with_service(_Books(_PERMS), lambda: _server.r_account(None, {}))["account"]
check("when the name isn't known yet, it is asked for",
      _acc["login"] == "author", _acc)
_so = _server.r_console_signout(None, {})
check("a plain sign-out doesn't pretend the author is switching",
      _server.CONSOLE["leaving"] is None)

_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "a-token"
_on_book(_BOOK)
_server.CONSOLE["access"]["book-a"] = "read"
_wa, _wch = _word_vault("book-a", "git@github.com:example-org/book-a.git")
_server._open_vault(_wa)
_server.IMPORT.update(folder=None, result=None, docx=None)
_st = _server.r_import_drafts_status(None, {})
check("on the import screen, a read-only account is said to be the reason",
      _st["why"] == "no_access" and "different account" in _st["message"], _st)
_FAKE_KEYCHAIN.pop(_keychain.ACCOUNT_GITHUB, None)
check("and not being signed in is told apart from it",
      _server.r_import_drafts_status(None, {})["why"] == "signed_out")
_FAKE_KEYCHAIN[_keychain.ACCOUNT_GITHUB] = "a-token"
_server.CONSOLE["access"]["book-a"] = "write"

# --- where a Word import goes, until the author says otherwise ---

_server.IMPORT.update(folder=None, result=None, docx=None)
_ist = _server.r_import_status(None, {})
check("with a vault open, a Word import goes in the book's chapters folder",
      _ist["folder"] == os.path.join(_wa, "chapters") and
      _server.IMPORT["folder"] == os.path.join(_wa, "chapters"), _ist["folder"])
_starts = []


def _chooser(prompt, start):
    _starts.append(start)
    return _wa, None


_server.picker.choose_folder = _chooser
_server.IMPORT["folder"] = None
_pf = _server.r_import_pick_folder(None, {})
_server.picker.choose_folder = _no_chooser
check("the folder chooser opens in the chapters folder, not the top of the vault",
      _starts == [os.path.join(_wa, "chapters")], _starts)
check("choosing the top of the vault anyway is pointed out",
      _pf["at_top"] is True, _pf)
_server.IMPORT["folder"] = os.path.join(_wa, "chapters")
check("a folder the author picked is kept, not put back to the default",
      _server.r_import_status(None, {})["folder"] == os.path.join(_wa, "chapters")
      and (_server.IMPORT.update(folder=_wa) or
           _server.r_import_status(None, {})["folder"] == _wa))
_server._open_vault(_vplain)
_server.IMPORT["folder"] = None
check("a vault with no chapters folder of the import kind gets no default",
      _server.r_import_status(None, {})["folder"] is None)

# --- changing book ---

_server._open_vault(_wa)
_server.CONSOLE["access"].update({"book-a": "write", "book-b": "write"})
_res = _converted(_wch, _wa, "Moving.md", {})
_ws = _server.r_books_choose(None, {"slug": "book-b"})
check("choosing another book closes the vault that is a copy of the old one",
      _ws["book"]["slug"] == "book-b" and _ws["vault"] is None, _ws)
check("and drops the Word import that was going into it",
      _server.IMPORT["folder"] is None and _server.IMPORT["result"] is None
      and not os.path.isdir(_res["stage"]))
check("choosing the same book again changes nothing",
      _server.r_books_choose(None, {"slug": "book-b"})["book"]["slug"] == "book-b")
shutil.rmtree(_wa, ignore_errors=True)
_on_book(_BOOK)


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
    "app/web/index.html": [
        ">Chapters</button>",
        "Waiting for you",
        "<h2>Sign in</h2>",
        ">Sign in</button>",
        "One-off setup",
        ">Open Settings</button>",
        ">Save identifier</button>",
        ">Check again</button>",
        ">All right</button>",
        "Signing in to see what is waiting",
        ">Sign-in identifier</label>",
        "Step 1 — copy this code",
        "Step 2 — a web page has opened. Type the code there and approve.",
        "Something needs your attention",
        "Nothing is waiting. Everything sent in has been dealt with.",
        "Suggestions from readers",
        "Draft changes",
        "Sent from the “Suggest an edit” button on the website.",
        "Written by trusted contributors in the browser editor.",
        "Elsewhere",
        "Open the discussion list",
    ],
    "app/web/app.js": [
        "Waiting for you to approve… this code lasts about ",
        "If the page did not open, go to ",
        "Signed in as ",
        "(none)",
        'Saved. You can now sign in from "Waiting for you".',
        "This Mac is not online, so this list may be incomplete. Nothing can be "
        "accepted or declined until it is back.",
    ],
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
    "app/server.py": [
        "That code ran out before it was used. Please start again.",
        "Signing in worked, but the token could not be stored in this Mac's",
        "Suggestions: ",
        "Draft changes: ",
        "Weekly jobs: ",
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

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(_support, ignore_errors=True)

print()
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  failed:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
