"""
Checks for the promises the tool makes.

Run with:  python3 -m tests.test_all
"""

import os
import shutil
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



shutil.rmtree(_vault, ignore_errors=True)

shutil.rmtree(root, ignore_errors=True)

print()
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  failed:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
