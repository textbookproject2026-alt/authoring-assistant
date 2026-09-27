# The platform's formatting rules

Version: 1
Updated: 27 Sep 2026

This file is the knowledge base for the Authoring Assistant's **AI formatting
check**. The app reads it at run time and sends it to DeepSeek as the rules to
check a chapter against, so changing a rule here changes the check with no code
change. Raise the version number, and the date, whenever a rule changes: the app
shows the version, so an author can tell which rules a check used.

Each rule is one list item that starts with its ID in backticks. The app groups
the proposed fixes by that ID, so keep IDs stable and never reuse an old one.
Where a rule comes from is in brackets at its end.

Sources:

- `app/convert.py`: how a Word chapter is converted (`pandoc -t gfm --wrap=none`)
  and what its report warns about.
- `textbook-template/docs/word-to-markdown.md`: how Word maps onto the markdown.
- `textbook-template/docs/editing-the-textbook.md`, "The markdown you'll actually
  meet": the marks that mean something in a chapter.

What the check may and may not do is fixed by the app, not by this file. It
proposes changes to one line at a time. It changes markup only, never a word,
and any proposal that changes the wording is thrown away.

## Headings

- `HEAD-1` The chapter title is a level-1 heading (`# Title`). There is exactly one,
  and it's the first heading in the file. It becomes the page's title on the site,
  in search and in the graph. [editing-the-textbook]
- `HEAD-2` Sections are `##` and subsections are `###`. Don't skip a level: a `####`
  directly under a `##` should be `###`. [word-to-markdown Part 1; convert.py
  heading report]
- `HEAD-3` A heading's hashes are the first thing on the line, followed by one space:
  `## Methods`, not `##Methods` or ` ## Methods`. [editing-the-textbook]
- `HEAD-4` A line that is only bold text standing alone as a section title
  (`**Introduction**`) was meant to be a heading. Make it a heading at the level
  its place in the outline needs, and drop the bold. [editing-the-textbook,
  "Breaks when"; convert.py "lines that stand alone"]
- `HEAD-5` A heading carries no bold or italic of its own: `## **Methods**` becomes
  `## Methods`. [editing-the-textbook]

## Emphasis

- `EMPH-1` Bold is `**bold**` and italic is `*italic*`. Use asterisks, not
  underscores, and close every mark you open on the same line. A stray single
  asterisk that runs to the end of a line is removed. [editing-the-textbook,
  "Breaks when"]
- `EMPH-2` Pandoc leftovers from Word, such as underline spans `[text]{.underline}`
  or small caps `[text]{.smallcaps}`, become plain text: keep `text`, drop the
  brackets and braces. [convert.py leftovers report]
- `EMPH-3` Empty Word bookmarks `[]{#_Toc123}` or `[]{#anything}` are removed.
  [convert.py leftovers report]

## Lists

- `LIST-1` A bulleted list item starts with `- ` (hyphen, space). A line starting
  with `•`, `*` or `+` followed by a space is changed to `- `. [editing-the-textbook]
- `LIST-2` A numbered list item starts with its number, a full stop and one space:
  `1. First`. `1)` becomes `1.`. [pandoc gfm output]

## Tables

- `TABLE-1` A table is a pipe table: every row starts and ends with `|`, and the
  second row is the separator row of `---` cells. [word-to-markdown Part 1;
  convert.py table report]
- `TABLE-2` A table row doesn't contain bold used only to fake a header row. The
  header row is the first row. [word-to-markdown "one header row at the top"]

## Footnotes, citations and references

- `NOTE-1` A footnote reference in the text is `[^n]`, and its note is a line
  starting `[^n]: `. Don't renumber, merge or remove footnotes: the two halves
  must keep matching labels. [editing-the-textbook; convert.py footnote report]
- `NOTE-2` A footnote reference sits straight after the word or punctuation it
  belongs to, with no space before it: `realism.[^3]`, not `realism. [^3]`.
  [pandoc gfm output]
- `CITE-1` Citations in the running text are author–year, as the author wrote
  them: `(Bhaskar, 1975)` or `Bhaskar (1975)`. Don't turn a citation into a link
  or add an anchor: the app's citation analysis does that, and only when it can
  find the reference entry. [app/references.py]
- `CITE-2` A reference-list entry is one paragraph on one line. Its title is in
  italics with `*` (`*Realist social theory*`), and a `^ref-...` marker at its end
  is never touched. [editing-the-textbook, footnotes and citations]

## Callouts

- `CALL-1` A callout's first line is `> [!type] Title`, with the type in lower case:
  `> [!Tip]` becomes `> [!tip]`. The types in use are `abstract`, `tip` and `info`.
  Leave any other type as it is and mention it in a note instead.
  [editing-the-textbook]
- `CALL-2` Every line inside a callout, including lines that look blank, starts
  with `>`. A line clearly inside a callout (between two `>` lines) without its
  `>` gets one. [editing-the-textbook, "Breaks when"]

## Links

- `LINK-1` A concept link is `[[Page Name]]`, or `[[Page Name|what the reader
  sees]]`. The page name is copied exactly from the concept page's file name,
  capitals included. Only correct the page name part of a link that is already
  there, and only to the exact name of a concept page that exists. Keep the words
  the reader sees: `[[retroduction]]` becomes `[[Retroduction|retroduction]]`,
  not `[[Retroduction]]`. Never add or remove a concept link: the app's
  concept-page analysis adds them. [editing-the-textbook]
- `LINK-2` A chapter-to-chapter link uses the file name without `.md`:
  `[[chapter-04|the light reactions]]`. [editing-the-textbook]
- `LINK-3` An ordinary web link is `[text](https://...)`. The address is never
  changed, added or removed. [convert.py]

## Pictures

- `IMG-1` A picture is `![description](assets/chapter-NN/imageN.png)` on its own
  line, with its folder named after the chapter. Don't change the path: the
  import put it there on purpose. An empty description may stay empty. [convert.py,
  "where the pictures go"]

## Whole-file conventions (the check reports these; it can't fix them)

These need lines added or removed, which the check never does. It may mention them
as notes, but it proposes no change for them.

- `FILE-1` One paragraph per line. A paragraph broken across several lines
  (hard-wrapped) is joined back into one. [convert.py, `--wrap=none`]
- `FILE-2` A blank line above every heading, list, table and callout, or it's
  swallowed into the paragraph before it. [editing-the-textbook]
- `FILE-3` Frontmatter, if any, is the very first thing in the file, between two
  `---` lines, and holds only the keys the platform reads: `tags`, `authors` (or
  `author`), `title`, `type`, `concept`, `aliases`, `description` and
  `paragraphNumbers`. [editing-the-textbook, "Tags, authors and concept pages"]
- `FILE-4` A chapter's file is `chapters/chapter-NN.md` (two digits), and its
  pictures are in `assets/chapter-NN/`. A book that was live before 27 Sep 2026
  keeps the names it has. [convert.py, "the one chapter-naming rule"]
