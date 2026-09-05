# Authoring Assistant

A small tool that tidies up the links in your chapters and helps you build a
glossary. It runs entirely on your own Mac.

---

## Installing it

1. Open the disk image you were sent.
2. Drag **Authoring Assistant** onto the **Applications** folder.
3. Double-click **Authoring Assistant** in your Applications folder.

That is everything. There is nothing to set up and nothing else to install.

Your web browser opens, and that is where you use the tool. Nothing else appears
on screen — no black windows, no icon bouncing in the Dock.

To use it again another day, double-click the same icon. You may want to drag it
onto your Dock so it is always to hand.

---

## What it does

It reads one chapter at a time and asks you three kinds of question, one at a
time.

**1. Citations.** It finds places where you have written something like
`(Bhaskar, 1975)` or `Bhaskar (1975)`, checks that a matching entry exists in
that chapter's own References section, and offers to turn the citation into a
link that jumps straight to that entry.

**2. Concept pages.** It finds places where your chapter mentions the title of
one of your concept pages, and offers to turn the mention into a link to that
page. It leaves alone anything in a heading, in a code block, in the reference
list, or already linked.

**3. Glossary terms.** It looks for terms worth defining: terms you introduce
with a phrase like "X is defined as", terms you put in **bold** the first time
you use them, and capitalised terms you use more than once. Ones you approve are
added to a single `glossary.md`, in alphabetical order.

It also does one thing that is not a question: it can turn a **Word document**
into a chapter in your vault. See "Bringing in a Word document" below.

---

## Using it

1. Double-click **Authoring Assistant**. Your browser opens.
2. Choose a single chapter, your whole vault folder, or a Word document to bring
   in. A normal Mac "choose a file" window appears — you never type a path.
3. If you chose a folder, pick the chapter you want from the list.
4. Answer the questions. Each one shows you the sentence, with the proposed
   change highlighted, and offers:
   - **Yes, make this change**
   - **No, leave it alone**
   - **Yes to every mention** of that term or citation
   - **No to every mention** of it

   A counter at the top shows how far along you are, like "12 of 40".
5. At the end you are shown exactly what will change — the changed lines side by
   side, and the whole chapter as it will be.
6. Tick the confirmation box and press **Save these changes**.
7. When you have finished, press **Quit** in the top corner, or simply close the
   browser tab. The tool stops on its own.

The tool asks where your chapters are every single time. It remembers nothing
between runs, so it can never surprise you by working on the wrong file.

---

## Things you should know

### There is no undo

This is deliberate. When you press **Save these changes**, your chapter is
rewritten and the old version is gone. The tool tells you this before you press
it, and you have to tick a box to confirm.

If that makes you uneasy, make a copy of the chapter in Finder before you start.
If your vault is backed up, or kept in version control, you already have a full
history and there is nothing to worry about.

### Close the chapter in Obsidian first

If the chapter is open in Obsidian with unsaved edits, those edits are not yet on
disk, and this tool cannot see them. Save the chapter in Obsidian (press
**Command** and **S** together) and close its tab before you run the tool.

The tool checks for this. It warns you if Obsidian is running, and if the file
changes on disk while you are working it **refuses to save** and tells you to
start again. It will not overwrite someone else's changes.

### It never reformats your chapters

The tool only ever rewrites the exact lines it is changing. Every other line is
left exactly as it was, character for character. It never re-wraps paragraphs,
never tidies spacing, and never reorders anything. If you accept twelve changes
across nine lines, exactly nine lines change.

### Running it twice is safe

If you run it again on the same chapter it will find nothing to do. It skips
citations that are already linked, concept pages that are already linked, and
terms already in your glossary, and it tells you what it skipped and why.

---

## Bringing in a Word document

If a chapter still lives in Word, the tool can turn it into a chapter in your
vault. Press **A Word document** on the first screen.

You choose three things: the Word document, the folder in your vault it should go
into, and what the chapter should be called. The name is filled in from the Word
file's own name, and you can change it.

Then you are shown **the whole converted chapter before anything is written**,
together with a list of the things worth checking. Nothing reaches your vault
until you have read it and ticked the box.

Your Word document is never changed and never moved. It stays exactly where it
is, and you can go back to it at any time.

### What to expect

Word can hold things that a chapter file cannot, so some parts come across better
than others. The tool tells you which, every time, for the document in front of
you. In general:

**Pictures** are taken out of the Word file and put in a folder next to the
chapter, named after it — a chapter called `Chapter 6.md` gets a folder called
`Chapter 6-media`. Obsidian finds them there on its own. Two things to watch for.
The picture files keep the meaningless names Word gave them inside the document,
which you may want to rename later. And **charts, SmartArt and pasted
spreadsheets are not really pictures** — Word draws them itself — so they come out
as files nothing outside Word can display, and show as broken pictures. The tool
warns you when it finds one. The fix is in Word: copy the chart, paste it back as
a picture, save, and bring the file in again.

Pictures with a caption, or sitting inside a paragraph, are written using web
tags — `<img src="…">` — rather than markdown's shorter form. This is normal.
Obsidian displays them correctly; they only look like code while you are editing.

**Tables** with ordinary cells come across as proper tables and look right. Column
widths, shading and colour are lost, because markdown cannot hold them. A table
whose cells have been **merged**, or where one cell holds more than one paragraph,
cannot become a markdown table at all: it is written as a block of web markup
instead. Nothing in it is lost, and Obsidian still shows it as a table — but it is
unpleasant to edit, and **the citation and concept-page checks skip over it
entirely**, so nothing inside such a table will ever be linked. If the table is
simple enough, it is worth unmerging the cells in Word and bringing the file in
again.

**Footnotes** come across, but not where they were. Word puts them at the foot of
each page; markdown has no pages, so they are all collected at the very bottom of
the chapter, with a number like `[^1]` where each one belonged. Obsidian shows
them as proper footnotes when you read the chapter. Worth scrolling to the end to
check the last one is complete. If a footnote number and its note do not match up
— usually because one was deleted in Word without the other — the tool says so.

**Headings** convert only if they were made with Word's **Heading styles**. If
the headings in your document were made by hand, by making the text bigger and
bold, Word considers them ordinary paragraphs and so does everything else. The
tool tells you when a document has no headings at all, and points out lines that
look like a heading written that way. You can put a `#` in front of each one in
Obsidian afterwards, but if there are many it is quicker to apply Heading 1 and
Heading 2 in Word and bring the file in again.

**Everything else.** Underlining, coloured text and highlighting have no markdown
equivalent and come across as markers like `[text]{.underline}`. Word bookmarks
appear as `[]{#name}` and show nothing when read. Equations from Word's equation
editor come across as maths between dollar signs; equations pasted in as pictures
stay pictures. Text boxes and sidebars become blocks of web markup. The tool
lists whichever of these it actually finds.

### Afterwards

A chapter fresh out of Word has no links in it at all, so the tool offers to go
through it straight away with the same three questions as ever — citations,
concept pages and glossary terms — so the chapter arrives linked rather than raw.
You can also say "not now" and do it another day; it is the same as choosing that
chapter from the front screen.

### The one thing that might need installing

To read Word documents the tool uses a separate free program called **pandoc**.
It normally comes inside the app, so there is nothing to do.

If your copy does not have it, the tool says so the first time you try to bring in
a Word document, and offers to install it for you: it downloads pandoc's own
installer, checks that it really is signed by the people who make it, and opens
it. An installer window appears — press **Continue**, then **Install**. It asks
for this Mac's password, which is normal for any installer. When it has finished,
come back and press **Check again**.

You are never asked to type a command. Everything else in the tool works as
normal whether or not pandoc is there — it is only needed for Word documents.

---

## The other half: what is waiting for you

At the top of the window there are two places to be: **Chapters** (everything
above) and **Waiting for you**.

"Waiting for you" is a readable window onto the things other people have sent in.
It exists so that you never have to visit a website to deal with them.

### Signing in, once

The first time, press **Sign in**. The tool shows you a short code and opens a
web page. Type the code into that page and approve it. That is the whole of it,
and you should not have to do it again on this Mac.

You are signing in **as yourself**. Anything you accept or decline is recorded
under your own name, and you can withdraw the tool's access at any time from your
account settings. Your sign-in is kept in this Mac's Keychain — not in a file,
and never in your vault.

If Alec has not yet put the sign-in identifier into **Settings**, the tool will
say so and there is nothing you can do until he has. It is a one-off.

### Suggestions from readers

These come from the **Suggest an edit** button on the website. A reader describes
what they think is wrong; they need no account, so most of these come from
ordinary readers.

Open one and you see who sent it, which page it is about, and what they said.
Then:

- **Accept** — you are taking it on. A thank-you is sent and it disappears from
  the list.
- **Decline, politely** — a courteous reply is sent saying the text is staying as
  it is. This is a perfectly good outcome; an answered "no" is far better than
  silence.
- **Open in my browser** — for anything unusual.

**When the tool can make the change for you.** Most suggestions are comments, and
a comment cannot be carried out mechanically — so the tool does not try. It tells
you plainly that this one is yours to do, and you make the change under Chapters.

Occasionally a reader writes an exact replacement, like *"the the domains" should
be "the three domains"*. If the old wording appears in that chapter **exactly
once**, the tool offers to make the change itself. It shows you the line as it is
and as it will be, and you tick a box. The same promise holds as everywhere else
in this tool: **only that one line changes**, and every other line of the chapter
is left exactly as it was. If the wording appears twice, or not at all, the tool
says so and leaves it to you rather than guessing.

For this to work the tool needs to know where your chapters are. There is a
**Choose my chapters folder** button at the bottom of the list.

### Draft changes

These are written by trusted contributors in the browser editor. **Nothing here
has reached readers.** Contributors have no way to publish; everything they write
waits for you.

Open one and you see, in ordinary before/after form, what wording they are
proposing to change. Then:

- **Accept this change** — it is folded into the drafts area, and the drafts are
  put in line for the live book. It has still reached no reader: sending it there
  is a separate press, under "Going live" below.
- **Decline it** — it is closed.
- **Open in my browser** — for anything large or unusual.

If a change is too big to read comfortably here, the tool says so and offers the
browser instead of printing a wall of text at you.

### Going live

Underneath the two lists is **Going live**: everything you have accepted, waiting
in one place. The drafts area is shared — anything written in the browser editor
is in there too — so what you see here is all of it together, and all of it is
what goes to readers.

Open it and you are shown how many changes there are, which pages they touch and
who wrote them. Tick the box and press **Publish to the live book**.

Three things are worth knowing.

**The tool never publishes on its own.** Accepting a change puts it in line and
stops there. Nothing reaches a reader until you press that button — because what
would go includes other people's work as well as the change you just accepted,
and you should see it before it goes.

**The site takes a few minutes to catch up.** It rebuilds itself after you
publish; readers see the change once it has.

**Your vault will be behind afterwards.** Publishing writes to the live book, and
your vault does not know about it. Take the latest into Obsidian before you write
there again, or your copy and the live book will disagree with each other.

If the same wording has been changed both in the drafts area and in the live book
— usually because you edited that line in Obsidian too — the tool says so plainly
and refuses to guess which one wins. Nothing is lost and nothing is undone. Sort
it out in your browser, or publish your own copy from Obsidian first and then
press **Check again**.

### The weekly jobs

Underneath is a line for each of the four jobs that run themselves every Sunday,
saying whether each one last finished properly. There is nothing to do here. If
one says it did not finish, tell Alec — it is not something you need to fix.

### Discussion and history

Two links out, at the bottom:

- **Reader discussion** — every comment left in the margins of the book, on one
  page. Reply to any of them in place on the live site.
- **History** — what changed, when, and by whom, plus each year's published
  edition.

Both open in your browser and are read-only.

### If you are not online

The list will tell you plainly that it could not be fetched. **Nothing can be
accepted, declined or published while you are offline** — the tool will not
pretend to have done something it has not. Everything under Chapters keeps working exactly as
normal, because none of it needs the internet.

---

## Settings

There is a **Settings** link in the top corner. There is nothing in it you have
to change.

It holds two things. The first is optional: if you have a **DeepSeek** account,
you can paste your key there and the tool will also ask DeepSeek for extra
glossary suggestions. You do not need one — the ordinary checks work perfectly well on
their own, and if DeepSeek is ever unavailable the tool quietly carries on
without it and tells you so.

The second is the **sign-in identifier** for "Waiting for you". Alec gives you
this once; it is not a password and not a secret.

Your DeepSeek key and your sign-in are both stored in this Mac's Keychain, not in
a file and never in your vault. The only thing ever sent to DeepSeek is the text
of the chapter you are working on, and only when you tick the box.

The first time the tool reads either of them after an update, macOS may ask
whether **Authoring Assistant** is allowed to use your Keychain. Say yes — it is
asking about its own saved items, not about anything else on the Mac.

---

## If something goes wrong

**The browser did not open.** Double-click the icon again.

**"This chapter changed on disk."** Something else edited the file while you were
answering questions — almost always Obsidian saving in the background. Nothing
was saved. Close the chapter in Obsidian and start again.

**A citation was not offered.** The tool only offers a citation when it can find
the matching entry in that chapter's References section. If it cannot, it says so
at the end and lists them — usually a sign that an entry is missing from your
reference list.

**No concept pages were found.** The tool looks for a folder called
`Definitions`, `Concepts`, `Terms` or similar inside your vault. If you keep them
somewhere else, choose your whole vault folder rather than a single chapter.

**Nothing at all was found.** That usually means you have already run it on that
chapter. The final screen explains what was skipped.

**"That file could not be read as a Word document."** The tool needs a `.docx` —
the kind Word has saved since 2007. An older `.doc`, or a file renamed to end in
`.docx`, will not work. Open it in Word and use **File**, then **Save As**, to
save it as a `.docx` first.

**"There is already a chapter called … in that folder."** The tool never writes
over a file that already exists, because there is no undo. Give the new chapter a
different name, or move the old one out of the way in Finder first.

**A picture shows as broken in the new chapter.** It was almost certainly a chart,
a SmartArt diagram or a pasted spreadsheet rather than a real picture. In Word,
right-click it, choose **Copy**, then **Paste Special** as a **Picture**, save, and
bring the file in again.
