# Authoring Assistant: the converter and the questions

This used to be a Mac app. Authors now use the author site
(<https://author.confused4now.org>), and the app's shell (its local server, web
page, file picker, preview and packaging) has been removed. What's left is the
Python both platform services run, at a pinned commit:

- **The author site** runs the questions in the browser with Pyodide: citations,
  concept links, glossary terms, the optional DeepSeek checks, and a reader
  suggestion's exact replacement. `author-site/converter.json` lists the files and
  pins the commit; its `scripts/fetch-converter.mjs` copies them at Pages build
  time.
- **book-requests' `import-chapter`** converts Word documents with `convert.py`,
  `contents.py` and `drafts.py`, at the `CONVERTER_REF` variable's commit.

Move either pin on deliberately, after the tests below pass at the new commit.

| File | What it does |
|---|---|
| `app/convert.py` | Word → Markdown with pandoc; the chapter-NN rule and `chapter-sources.json` |
| `app/contents.py` | a new chapter's line under "Contents" on the front page |
| `app/drafts.py` | what a send to the drafts branch changes |
| `app/session.py` | one chapter's questions, from reading to the edited text |
| `app/references.py`, `terms.py`, `glossary.py` | citations, concept links, glossary terms |
| `app/mdmap.py`, `edits.py` | the line map and edits that never touch other lines |
| `app/console.py` | a reader's suggestion and its exact replacement |
| `app/formatting.py`, `formatting_rules.md`, `llm.py` | the optional DeepSeek checks; the rules file is the knowledge base |
| `app/github.py`, `registry.py`, `config.py`, `keychain.py` | what the modules above import. On the author site, `keychain` is replaced by a stand-in (`author-site/site/lib/python.js`) |

## Tests

```sh
python3 tests/test_all.py
```

The test file is plain Python with no dependencies. It runs pandoc's real
conversion only when pandoc is installed, and says when it skips. author-site's
`questions` job also checks that Pyodide's output matches this Python's, byte for
byte.
