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

---

## Using it

1. Double-click **Authoring Assistant**. Your browser opens.
2. Choose either a single chapter or your whole vault folder. A normal Mac
   "choose a file" window appears — you never type a path.
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

- **Accept this change** — it goes into the drafts area. It still is not live: it
  reaches readers when you next publish from Obsidian.
- **Decline it** — it is closed.
- **Open in my browser** — for anything large or unusual.

If a change is too big to read comfortably here, the tool says so and offers the
browser instead of printing a wall of text at you.

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
accepted or declined while you are offline** — the tool will not pretend to have
done something it has not. Everything under Chapters keeps working exactly as
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
