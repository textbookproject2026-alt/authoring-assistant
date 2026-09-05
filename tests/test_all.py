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

from app import console as _console  # noqa: E402
from app import github as _github  # noqa: E402
from app import keychain as _keychain  # noqa: E402

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

import json  # noqa: E402

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


def _posts(service):
    return [c for c in service.calls if c[0] == "POST" and c[1].endswith("/pulls")]


_svc = _Service()
_r1 = _with_service(_svc, lambda: _server.r_console_draft_accept(
    None, {"number": 5, "title": "Fix a typo in chapter 3"}))

check("accepting still folds the change into the drafts area, squashed",
      ("PUT", f"{_github.API}/repos/{_github.OWNER}/{_github.REPO}/pulls/5/merge",
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
    None, {"number": 6, "title": "Another change"}))
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
    None, {"number": 7, "title": "A clashing change"}))
check("a clash with the live book is surfaced rather than failing silently",
      "cannot be published as it stands" in (_r3["warning"] or ""), _r3["warning"])
check("a clash still leaves the change safe in the drafts area",
      _r3["publish"]["can_publish"] is False and
      any("folded into the drafts area" in t for t in _r3["steps"]), _r3["steps"])

# "Not known yet" is a real answer and is given as one.
_unsure = _Service(mergeable=None, state="unknown")
_state_unknown = _with_service(
    _unsure, lambda: _github.mergeability("t", 77, tries=1))
check("an unsettled pull request is reported as unknown, never as fine",
      _state_unknown == "unknown", _state_unknown)
check("an unknown state does not offer to publish",
      _console.describe_publish({"number": 77}, _COMPARE,
                                "unknown")["can_publish"] is False)

# Publishing is its own deliberate press.
_pub = _Service()
_pub.open_prs.append({"number": 77, "html_url": "u",
                      "created_at": "2026-09-01T00:00:00Z"})
_refused = None
try:
    _with_service(_pub, lambda: _server.r_console_publish(None, {"number": 77}))
except KeyError as e:
    _refused = str(e.args[0])
check("publishing without ticking the box is refused",
      _refused is not None and "tick the box" in _refused, _refused)
check("nothing was merged when it was refused", _pub.merged == [], _pub.merged)

_wrong = None
try:
    _with_service(_pub, lambda: _server.r_console_publish(
        None, {"number": 999, "confirm": True}))
except KeyError as e:
    _wrong = str(e.args[0])
check("publishing anything but the request the author was shown is refused",
      _wrong is not None and "has changed since this screen" in _wrong, _wrong)
check("and nothing was merged when it was refused", _pub.merged == [], _pub.merged)

_done = _with_service(_pub, lambda: _server.r_console_publish(
    None, {"number": 77, "confirm": True}))
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
                    lambda: _server._publish_state("t")) == (None, None))


check("the scope asked for excludes the author's private work",
      _github.SCOPE == "public_repo", _github.SCOPE)
check("all four weekly jobs are known to the console",
      len(_github.WEEKLY_JOBS) == 4, len(_github.WEEKLY_JOBS))

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

print()
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  failed:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
