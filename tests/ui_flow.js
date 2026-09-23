/* Loads the real app.js against a stub browser and drives the review flow,
   to check the Yes / No / Yes-to-all logic and the progress counter. */
const fs = require('fs'), vm = require('vm'), path = require('path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'app/web/index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'app/web/app.js'), 'utf8');

const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);

function mkEl(id) {
  const e = {
    id, textContent: '', _html: '', value: '', checked: false, disabled: false,
    style: {}, dataset: {}, children: [], parentElement: null,
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { on === undefined ? (this._s.has(c) ? this._s.delete(c) : this._s.add(c)) : (on ? this._s.add(c) : this._s.delete(c)); },
    },
    appendChild(c) { this.children.push(c); return c; },
    // As in a browser, emptying the markup empties the children too.
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; if (v === '') this.children = []; },
    querySelectorAll() { return []; },
  };
  e.parentElement = { style: {} };
  return e;
}

const els = {};
ids.forEach(i => els[i] = mkEl(i));

const steps = ['step-choose','step-chapter','step-options','step-working','step-review',
               'step-preview','step-done','step-stopped',
               'step-import','step-import-setup','step-import-preview',
               'step-import-done',
               'step-console','step-suggestion','step-draft','step-publish',
               'step-console-done','step-books','step-console-setup',
               'step-console-signin'];
steps.forEach(s => els[s].classList.add('step'));

const groupSpans = [mkEl('grp1'), mkEl('grp2')];
const secondary = mkEl('secondary'); secondary.style = {};
const warnbox = mkEl('warnbox');
const previewH2 = mkEl('previewh2');

const document = {
  body: { dataset: { token: 'T', build: '1.0.0+b1' } },
  getElementById: id => els[id] || (els[id] = mkEl(id)),
  createElement: tag => mkEl(tag),
  createTextNode: text => ({ nodeText: String(text) }),
  querySelector: sel => {
    if (sel === '.answers.secondary') return secondary;
    if (sel === '.warnbox') return warnbox;
    if (sel === '#step-preview h2') return previewH2;
    if (sel === 'input[name=anchor]:checked') return { value: 'obsidian' };
    return mkEl(sel);
  },
  querySelectorAll: sel => {
    if (sel === '.step') return steps.map(s => els[s]);
    if (sel === '.grp') return groupSpans;
    if (sel === '.tab') return [];
    if (sel === '[data-back]') return [];
    return [];
  },
};

// A fake server: three findings, two of them the same concept page.
const FINDINGS = [
  { id: 'r0', kind: 'reference', group: 'ref-a', group_label: 'Archer (1995)', line_no: 5,
    before: 'Structures shape action (', match: 'Archer, 1995', after: ').',
    becomes: '[Archer, 1995](#^ref-a)', occurrence: 1, occurrence_total: 1,
    title: 'Citation of Archer (1995)', explain: 'Link it.', detail: '', detail_label: '' },
  { id: 't1', kind: 'term', group: 'term::Emergence', group_label: 'Emergence', line_no: 7,
    before: 'The idea of ', match: 'emergence', after: ' matters.',
    becomes: '[[Emergence|emergence]]', occurrence: 1, occurrence_total: 3,
    title: 'Mention of "Emergence"', explain: 'Link it.', detail: '', detail_label: '' },
  { id: 't2', kind: 'term', group: 'term::Emergence', group_label: 'Emergence', line_no: 9,
    before: 'Again ', match: 'emergence', after: ' here.',
    becomes: '[[Emergence|emergence]]', occurrence: 2, occurrence_total: 3,
    title: 'Mention of "Emergence"', explain: 'Link it.', detail: '', detail_label: '' },
  { id: 'g3', kind: 'glossary', group: 'glossary::morphogenesis', group_label: 'morphogenesis',
    line_no: 11, before: '', match: 'morphogenesis', after: '', becomes: '',
    occurrence: 1, occurrence_total: 1, title: 'Glossary entry', explain: 'Add it.',
    detail: 'Structural elaboration.', detail_label: 'Suggested wording' },
];

// Which book the fake server is on. The page must follow it, never assume one.
const BOOK_A = {
  slug: 'book-a', title: 'Book A', repo: 'example-org/book-a', access: 'write',
  status: 'live', site: null, discussion_url: null,
  history_url: 'https://github.com/example-org/book-a/commits/main',
};
const BOOK_B = Object.assign({}, BOOK_A, {
  slug: 'book-b', title: 'Book B', repo: 'example-org/book-b',
  discussion_url: 'https://hypothes.is/search?q=url:https://b.example/*',
  history_url: 'https://github.com/example-org/book-b/commits/published',
});
const WS_NONE = { book: null, vault: null, locked: false, can_write_vault: false,
                  registry: { source: 'live', fresh: true, as_of: '17 September 2026' } };
const WS_A = { book: BOOK_A, vault: null, locked: false, can_write_vault: false,
               registry: { source: 'cached', fresh: false, as_of: '16 September 2026' } };
const WS_A_VAULT = { book: BOOK_A, locked: true, can_write_vault: true,
                     vault: { name: 'Vault A', root: '/va', state: 'ok', message: '' },
                     registry: { source: 'live', fresh: true } };
const WS_B = Object.assign({}, WS_A, { book: BOOK_B });
let CURRENT_WS = WS_NONE;
const bodies = {};

let lastPreviewBody = null;
let lastConvertBody = null;
// What the fake server says about sending to the drafts area. Changed as the
// run goes on: no push rights first, then an author who can push.
let DRAFTS_STATUS = { available: false,
  message: 'Your account can\'t make changes to “Book A” (example-org/book-a), so this chapter can\'t be sent to its drafts area.' };
const DRAFTS_LOOK = {
  repo: 'example-org/book-a', branch: 'drafts', head: 'h1',
  chapter_path: 'chapters/Chapter 6.md', media_dir: 'assets/Chapter 6',
  refused: null, exists: false, last: null, nothing_to_send: false,
  removed: [], changed_lines: null,
};
const DRAFTS_MOVED = Object.assign({}, DRAFTS_LOOK, {
  head: 'h2', exists: true, removed: ['image2.png'],
  changed_lines: { removed: 3, added: 4 },
  last: { who: 'cms-user', when: '2026-09-22T10:00:00Z', message: 'Update Chapter 6' },
});
let sendReplies = [];
// The drafts area as the Chapters tools and the console see it (§8 step 2).
let commitReplies = [];
let acceptReplies = [];
let planReply = null;       // in place of PLAN, when set
let lastHeaders = null;
let wrongBuild = null;      // how an app of another version answers, when set
const PLAN = {
  can_apply: true, reason: '', line_no: 3, before: 'The the words are here.',
  after: 'The words are here.', head: 'h1', branch: 'drafts',
  vault: { name: 'Vault A', can_apply: true, reason: '', suggested: true },
};
// Signing out and in again. The fake server refuses a sign-out when told to,
// as the real one does when the Keychain still has the token afterwards.
let SIGNED_IN = true;
let signoutReply = null;
let signinReply = { waiting: true, wait: 5 };
// Flipped part-way through the run, to check the screen shown when the
// converter is missing and the one shown once it has been installed.
let IMPORT_READY = { ready: false, where: null, version: null, can_install: true };
const calls = [];
const fetch = async (route, opts) => {
  calls.push(route);
  lastHeaders = opts.headers || {};
  if (wrongBuild && !route.startsWith('/api/ping')) {
    const w = wrongBuild;
    return { ok: false, status: w.status, json: async () => w.body };
  }
  const body = JSON.parse(opts.body || '{}');
  bodies[route] = body;
  if (route === '/api/books/choose') CURRENT_WS = body.slug === 'book-b' ? WS_B : WS_A;
  if (route === '/api/vault/close' || route === '/api/books/switch') CURRENT_WS = WS_A;
  const reply = {
    '/api/env': {
      pandoc: true, deepseek: false, deepseek_hint: null, obsidian_running: false,
      python: '3.12.14', version: '1.0.0', seen_welcome: true,
      support_dir: '/Users/x/Library/Application Support/Authoring Assistant',
      key_file: '/Users/x/Library/Application Support/Authoring Assistant/deepseek.key',
    },
    '/api/welcome-done': { ok: true },
    '/api/ping': { ok: true },
    '/api/save-key': { saved: true, hint: 'abcd', deepseek: true },
    '/api/test-key': { ok: true, message: 'The key works. DeepSeek answered normally.' },
    '/api/clear-key': { cleared: true, deepseek: false },
    '/api/quit': { stopping: true },
    '/api/analyse': { findings: FINDINGS, notes: ['A note for the author.'] },

    '/api/import/status': IMPORT_READY,
    '/api/import/pick-docx': {
      docx: '/Users/x/Documents/Chapter 6.docx', docx_name: 'Chapter 6.docx',
      suggested_name: 'Chapter 6.md', size: 41000,
    },
    '/api/import/pick-folder': {
      folder: '/v/Chapters', folder_name: 'Chapters', chapters_here: 5,
    },
    '/api/import/convert': (() => { lastConvertBody = body; return {
      name: 'Chapter 6.md', path: '/v/Chapters/Chapter 6.md',
      text: '# Chapter Six\n\nA paragraph.\n', folder: '/v/Chapters',
      media_rel: 'assets/Chapter 6',
      media: [{ rel: 'rId1.png', name: 'rId1.png', ext: 'png', size: 2048 }],
      local_problem: null, drafts: DRAFTS_STATUS,
      counts: { lines: 3, words: 4, pictures: 1, headings: 1,
                pipe_tables: 0, html_tables: 1, footnotes: 2 },
      notes: [
        { level: 'ok', headline: '1 picture was taken out of the Word file',
          body: 'It is in "assets/Chapter 6".', check: 'Look at it.' },
        { level: 'warn', headline: '1 table could not be made into a proper table',
          body: 'Its cells were merged.', check: 'Unmerge them in Word.' },
      ],
    }; })(),
    '/api/import/save': {
      chapter: '/v/Chapters/Chapter 6.md', chapter_name: 'Chapter 6.md',
      media: '/v/assets/Chapter 6', media_name: 'assets/Chapter 6',
      folder: '/v/Chapters',
    },
    '/api/import/cancel': { ok: true },
    '/api/import/drafts-status': DRAFTS_STATUS,
    '/api/import/drafts-check': DRAFTS_LOOK,
    '/api/import/drafts-send': route === '/api/import/drafts-send'
      ? sendReplies.shift() : null,

    '/api/workspace': CURRENT_WS,
    '/api/books': { books: [BOOK_A], hidden: 2, offline: false, note: '',
                    workspace: CURRENT_WS },
    '/api/books/choose': CURRENT_WS,
    '/api/vault/close': CURRENT_WS,
    '/api/books/switch': CURRENT_WS,
    '/api/account': CURRENT_WS,
    '/api/console/signout': route === '/api/console/signout'
      ? (signoutReply ? Object.assign({}, signoutReply, { workspace: CURRENT_WS }) : null) : null,
    '/api/console/signin-start': { code: 'ABCD-1234', url: 'https://github.com/login/device', minutes: 15 },
    '/api/console/signin-poll': signinReply,
    '/api/console/status': { configured: true, signed_in: SIGNED_IN, who: 'The Author',
                             login: SIGNED_IN ? 'author' : null,
                             keychain: true, workspace: CURRENT_WS },
    '/api/console/load': {
      book: CURRENT_WS.book, workspace: CURRENT_WS,
      who: 'The Author', suggestions: [], drafts: [], weekly: [],
      problems: [], offline: false,
      publish: {
        open: true, waiting: false, number: 77,
        url: 'https://example.invalid/77', when: '2026-09-01T00:00:00Z',
        pages: ['chapter-03', 'chapter-09'], page_count: 2, change_count: 2,
        who: ['ada', 'Textbook CMS'], state: 'clean',
        state_words: 'This can go to readers now. Nothing else is waiting on it.',
        can_publish: true,
      },
    },
    '/api/console/publish': { done: true, steps: [
      'The drafts were sent to the live book.',
      'The site rebuilds itself from there, which takes a few minutes. Readers see the change once it has.',
      'Your vault does not know about this yet. Take the latest into Obsidian before you write there again, or your copy and the live book will disagree with each other.',
    ] },
    '/api/open': {
      session_id: 'S1', mode: 'file', root: '/v', root_name: 'v',
      chapters: [{ path: '/v/Chapters/Chapter 6.md', rel: 'Chapter 6.md',
                   name: 'Chapter 6', folder: '', size: 30 }],
      glossary_path: '/v/glossary.md', glossary_exists: false,
    },
    '/api/prepare': body.session_id === 'D1' ? {
      mode: 'drafts', head: 'd1',
      chapter: 'chapters/Only.md', chapter_name: 'Only.md',
      warnings: [], blockers: [], hard_wrapped: false, lines: 3,
      has_references: true, concept_pages: ['Emergence'],
      concept_source: 'chapters/Definitions', deepseek: false,
    } : {
      mode: 'file', head: null,
      chapter: '/v/Chapters/Chapter 6.md', chapter_name: 'Chapter 6.md',
      warnings: [], blockers: [], hard_wrapped: false, lines: 3,
      has_references: true, concept_pages: ['Emergence'],
      concept_source: 'Definitions', deepseek: false,
    },
    '/api/drafts/open': {
      session_id: 'D1', mode: 'drafts', root: null,
      root_name: 'the drafts area of “Book A”', repo: 'example-org/book-a',
      branch: 'drafts', head: 'd1', workspace: CURRENT_WS,
      chapters: [{ path: 'chapters/Only.md', rel: 'chapters/Only.md', name: 'Only',
                   folder: 'chapters', size: null }],
    },
    '/api/commit': route === '/api/commit' ? commitReplies.shift() : null,
    '/api/drafts/download': {
      folder: '/Users/x/book-a (drafts, 2026-09-22)', files: 12, left_out: 0,
      repo: 'example-org/book-a', branch: 'drafts',
    },
    '/api/console/plan': planReply || PLAN,
    '/api/console/accept': route === '/api/console/accept' ? acceptReplies.shift() : null,
    '/api/preview': (() => { lastPreviewBody = body; return {
      counts: { references: 1, terms: 2, glossary: 1, expanded: 1 },
      diff: [{ line_no: 5, before: 'a', after: 'b' }],
      new_text: 'whole file', glossary_path: '/v/glossary.md', glossary_exists: false,
      glossary_added: ['morphogenesis'], glossary_after: '# Glossary\n\n## morphogenesis\n\nStructural elaboration.\n',
      changed_lines: [4],
    }; })(),
  }[route] || {};
  if (reply && reply.__refuse) {
    return { ok: false, status: 400, json: async () => ({ error: reply.__refuse }) };
  }
  if (route === '/api/console/signout' && reply && !reply.__refuse) SIGNED_IN = false;
  return { ok: true, json: async () => reply };
};

const listeners = {};
let beacons = 0;
const ctx = {
  document, fetch, console, setInterval, clearInterval, setTimeout, clearTimeout,
  encodeURIComponent,
  navigator: { sendBeacon: () => { beacons++; return true; } },
  window: {
    scrollTo() {},
    open() {},
    addEventListener: (name, fn) => { listeners[name] = fn; },
  },
};
vm.createContext(ctx);
vm.runInContext(js.replace('window.scrollTo(0, 0);', ''), ctx);

// --- drive the flow ---------------------------------------------------------
const results = [];
function check(name, cond, got) {
  results.push([name, cond, got]);
  console.log(`[${cond ? '  ok  ' : ' FAIL '}] ${name}${cond ? '' : '   got: ' + JSON.stringify(got)}`);
}

(async () => {
  // boot() runs on load: it reads the environment and picks the first screen.
  await new Promise(r => setTimeout(r, 30));
  check('the tool asks the server about this Mac on startup',
        calls.includes('/api/env'), calls);
  check('a returning author goes straight to the chooser, not the welcome screen',
        !els['step-choose'].classList.contains('hidden'), 'welcome shown instead');
  check('the heartbeat starts immediately so the server knows the page is open',
        calls.some(c => c.startsWith('/api/ping')), calls);
  check('DeepSeek is offered but switched off when no key is saved',
        els['opt-deepseek'].disabled === true, els['opt-deepseek'].disabled);

  // settings panel
  await ctx.document.getElementById('open-settings').onclick();
  check('the settings panel opens',
        !els['settings'].classList.contains('hidden'), 'still hidden');
  check('the settings panel says the key is kept in the Keychain, not a file',
        els['key-file'].textContent.includes('Keychain'),
        els['key-file'].textContent);
  check('the settings panel lists what is on this Mac',
        els['env-list'].children.length === 4, els['env-list'].children.length);

  els['key-input'].value = 'sk-test-key';
  await els['key-save'].onclick();
  check('saving a key checks it works straight away',
        calls.includes('/api/save-key') && calls.includes('/api/test-key'), calls);
  check('the author is told the key works',
        els['key-message'].textContent.includes('works'), els['key-message'].textContent);
  await els['key-clear'].onclick();
  check('the key can be removed again',
        calls.includes('/api/clear-key'), calls);
  els['settings-close'].onclick();

  // closing the tab must tell the server to stop
  listeners['pagehide']();
  check('closing the page tells the tool to shut down', beacons === 1, beacons);

  els['opt-references'].checked = true;
  els['opt-terms'].checked = true;
  els['opt-glossary'].checked = true;
  els['opt-first'].checked = true;
  els['opt-deepseek'].checked = false;

  await els['start-analysis'].onclick();
  await new Promise(r => setTimeout(r, 20));

  check('progress starts at "1 of 4"', els['progress-label'].textContent === '1 of 4',
        els['progress-label'].textContent);
  check('the sentence is shown with the citation highlighted',
        els['sentence-before'].innerHTML.includes('<mark>Archer, 1995</mark>'),
        els['sentence-before'].innerHTML);
  check('the "after" line shows the proposed change',
        els['sentence-after'].innerHTML.includes('[Archer, 1995](#^ref-a)'),
        els['sentence-after'].innerHTML);

  els['ans-yes'].onclick();
  check('after answering, progress reads "2 of 4"', els['progress-label'].textContent === '2 of 4',
        els['progress-label'].textContent);
  check('the second question is the concept page', els['finding-title'].textContent.includes('Emergence'),
        els['finding-title'].textContent);
  check('the "yes to all" buttons name the term', groupSpans[0].textContent === '"Emergence"'.replace(/"/g, '“').slice(0, 0) || groupSpans[0].textContent.includes('Emergence'),
        groupSpans[0].textContent);

  els['ans-yes-all'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('"yes to all" skips the other mention of the same term and moves to the glossary question',
        els['finding-title'].textContent.includes('Glossary'), els['finding-title'].textContent);
  check('progress jumps past the skipped question', els['progress-label'].textContent === '4 of 4',
        els['progress-label'].textContent);
  check('the glossary question hides the "after" card',
        els['after-card'].classList.contains('hidden'), 'not hidden');

  els['ans-no'].onclick();
  await new Promise(r => setTimeout(r, 30));

  check('all four answers were recorded and sent',
        lastPreviewBody && lastPreviewBody.accepted.length === 3,
        lastPreviewBody && lastPreviewBody.accepted);
  check('the rejected glossary term was not sent',
        lastPreviewBody && !lastPreviewBody.accepted.includes('g3'),
        lastPreviewBody && lastPreviewBody.accepted);
  check('"yes to all" asked the server to link every mention',
        lastPreviewBody && lastPreviewBody.expand_groups.includes('Emergence'),
        lastPreviewBody && lastPreviewBody.expand_groups);
  check('the preview screen is showing',
        !els['step-preview'].classList.contains('hidden'), 'hidden');
  check('the save button starts disabled until the box is ticked',
        els['do-commit'].disabled === true, els['do-commit'].disabled);
  check('the notes from the analysis are shown',
        els['notes-list'].children.length === 1, els['notes-list'].children.length);

  els['confirm-box'].checked = true;
  els['confirm-box'].onchange({ target: { checked: true } });
  check('ticking the box enables saving', els['do-commit'].disabled === false,
        els['do-commit'].disabled);

  // --- bringing in a Word document ------------------------------------------

  await els['pick-docx'].onclick();
  check('with no converter on the Mac, the author is offered the install, not an error',
        !els['step-import-setup'].classList.contains('hidden'), 'wrong screen');
  check('the install screen never tells the author to type a command',
        !/terminal|brew|command line/i.test(html.split('step-import-setup')[1]
          .split('</section>')[0]), 'a command appears on the install screen');

  IMPORT_READY = { ready: true, where: 'bundled', version: '3.11', can_install: false };
  await els['install-recheck'].onclick();
  check('once the converter is there, the author goes on to the import screen',
        !els['step-import'].classList.contains('hidden'), 'wrong screen');
  check('nothing can be converted before a document and a folder are chosen',
        els['do-convert'].disabled === true, els['do-convert'].disabled);

  await els['choose-docx'].onclick();
  check('choosing a Word document fills in a suggested chapter name',
        els['import-name'].value === 'Chapter 6.md', els['import-name'].value);
  check('it is still not ready, because there is nowhere to put it',
        els['do-convert'].disabled === true, els['do-convert'].disabled);

  await els['choose-import-folder'].onclick();
  check('choosing a folder says how many chapters are already there',
        els['folder-chosen'].textContent.includes('5 chapters'),
        els['folder-chosen'].textContent);
  check('with all three chosen, converting is offered',
        els['do-convert'].disabled === false, els['do-convert'].disabled);

  els['import-name'].value = 'Chapter 6.md';
  await els['do-convert'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('the chosen name is what gets converted',
        lastConvertBody && lastConvertBody.name === 'Chapter 6.md',
        lastConvertBody);
  check('the result is shown before anything is written',
        !els['step-import-preview'].classList.contains('hidden'), 'wrong screen');
  check('every note about what to check is shown',
        els['import-notes'].children.length === 2,
        els['import-notes'].children.length);
  check('the whole converted chapter is shown, not a summary of it',
        els['import-text'].textContent.includes('A paragraph.'),
        els['import-text'].textContent);
  check('the pictures are listed with where they will go',
        els['import-media-list'].children.length === 1 &&
        els['import-media-note'].textContent.includes('assets/Chapter 6'),
        els['import-media-note'].textContent);
  check('saving is refused until the author says they have looked',
        els['do-import-save'].disabled === true, els['do-import-save'].disabled);

  els['import-confirm'].onchange({ target: { checked: true } });
  check('ticking the box allows saving', els['do-import-save'].disabled === false,
        els['do-import-save'].disabled);

  await els['do-import-save'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('after saving, the author is told where the chapter and pictures went',
        !els['step-import-done'].classList.contains('hidden') &&
        els['import-done-summary'].children.length > 0, 'nothing shown');
  const doneText = (function gather(n) {
    return (n.textContent || '') + (n.children || []).map(gather).join(' ');
  })(els['import-done-summary']);
  check('and the pictures are named by their place in the textbook, not beside the chapter',
        doneText.includes('assets/Chapter 6'), doneText);

  await els['import-analyse'].onclick();
  await new Promise(r => setTimeout(r, 30));
  check('the new chapter can go straight into the three analyses',
        calls.includes('/api/open') && calls.includes('/api/prepare'), calls);
  check('and it lands on the options screen, ready to be looked through',
        !els['step-options'].classList.contains('hidden'), 'wrong screen');

  // --- sending a Word import to the drafts area -----------------------------

  const said = n => [n.textContent, ...n.children.map(said)].join(' ');
  check('an author without push rights is told before converting, on the import screen',
        els['import-drafts-status'].textContent.includes("can't make changes"),
        els['import-drafts-status'].textContent);
  check('and then no send to drafts is offered for that chapter',
        els['do-send-drafts'].disabled === true, els['do-send-drafts'].disabled);

  DRAFTS_STATUS = { available: true,
    message: 'Once it is converted you can send it to the drafts area of “Book A”.' };
  await els['import-another'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('an author who can push is told where it can go, before converting',
        els['import-drafts-status'].textContent.includes('drafts area of “Book A”'),
        els['import-drafts-status'].textContent);
  // Choosing the folder opens the vault, and the vault decides the book.
  CURRENT_WS = WS_A_VAULT;
  await els['choose-import-folder'].onclick();
  await els['choose-docx'].onclick();
  els['import-name'].value = 'Chapter 6.md';
  await els['do-convert'].onclick();
  await new Promise(r => setTimeout(r, 30));
  check('after converting, the drafts area is looked at for the current book',
        calls.includes('/api/import/drafts-check') &&
        bodies['/api/import/drafts-check'].book === 'book-a',
        bodies['/api/import/drafts-check']);
  check('the author is shown where on drafts the chapter will go',
        els['import-drafts-where'].textContent.includes('chapters/Chapter 6.md') &&
        els['import-drafts-where'].textContent.includes('“drafts”'),
        els['import-drafts-where'].textContent);
  check('nothing is sent until the author says they have looked',
        els['do-send-drafts'].disabled === true, els['do-send-drafts'].disabled);
  els['import-confirm'].checked = true;
  els['import-confirm'].onchange({ target: els['import-confirm'] });
  check('ticking the box offers both ways: the vault and the drafts area',
        els['do-send-drafts'].disabled === false && els['do-import-save'].disabled === false,
        [els['do-send-drafts'].disabled, els['do-import-save'].disabled]);

  sendReplies = [{ moved: true, message: 'Nothing was sent. Something else changed the drafts area.',
                   drafts: DRAFTS_MOVED }];
  await els['do-send-drafts'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('the send carries the book and the drafts it was shown',
        bodies['/api/import/drafts-send'].book === 'book-a' &&
        bodies['/api/import/drafts-send'].head === 'h1', bodies['/api/import/drafts-send']);
  check('if drafts moved, the author stays on the chapter and is told nothing was sent',
        !els['step-import-preview'].classList.contains('hidden') &&
        els['import-drafts-problem'].textContent.includes('Nothing was sent'),
        els['import-drafts-problem'].textContent);
  check('the fresh offer shows who changed the chapter and what sending replaces',
        els['import-drafts-detail'].textContent.includes('cms-user') &&
        els['import-drafts-detail'].textContent.includes('3 lines taken out, 4 put in'),
        els['import-drafts-detail'].textContent);
  check('and names the picture that would be taken out',
        said(els['import-drafts-removed']).includes('image2.png'),
        said(els['import-drafts-removed']));
  check('the author has to look and say yes again',
        els['import-confirm'].checked === false && els['do-send-drafts'].disabled === true,
        [els['import-confirm'].checked, els['do-send-drafts'].disabled]);
  els['import-confirm'].checked = true;
  els['import-confirm'].onchange({ target: els['import-confirm'] });
  check('replacing a chapter on drafts needs its own tick',
        !els['import-replace-label'].classList.contains('hidden') &&
        els['do-send-drafts'].disabled === true, els['do-send-drafts'].disabled);
  els['import-replace'].checked = true;
  els['import-replace'].onchange();
  check('with both ticks, sending is offered again',
        els['do-send-drafts'].disabled === false, els['do-send-drafts'].disabled);

  sendReplies = [{ sent: true, sha: 'c0ffee', url: 'https://example.invalid/commit/c0ffee',
                   repo: 'example-org/book-a', branch: 'drafts',
                   chapter_path: 'chapters/Chapter 6.md', files: 1, removed: 1, saved: null }];
  await els['do-send-drafts'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('the second send goes on the fresh offer, replacing on purpose',
        bodies['/api/import/drafts-send'].head === 'h2' &&
        bodies['/api/import/drafts-send'].replace === true, bodies['/api/import/drafts-send']);
  check('once sent, the author is told it is in the drafts area, not the vault',
        !els['step-import-done'].classList.contains('hidden') &&
        els['import-done-title'].textContent === 'The chapter is in the drafts area',
        els['import-done-title'].textContent);
  check('and where it went, and that readers do not see it yet',
        said(els['import-done-summary']).includes('chapters/Chapter 6.md') &&
        said(els['import-done-summary']).includes("Readers don't see it"),
        said(els['import-done-summary']));
  check('the folder route is still there: it can be saved into the vault as well',
        !els['import-other-way'].classList.contains('hidden') &&
        els['import-other-way'].textContent === 'Save it into your vault as well',
        els['import-other-way'].textContent);
  check('going through the chapter is only offered once it is in the vault',
        els['import-analyse'].classList.contains('hidden'), 'offered');
  els['import-other-way'].onclick();
  check('going back to save it asks for the tick again',
        !els['step-import-preview'].classList.contains('hidden') &&
        els['import-confirm'].checked === false && els['do-send-drafts'].disabled === true,
        els['import-confirm'].checked);
  CURRENT_WS = WS_NONE;
  await ctx.refreshWorkspace();

  // --- the console: accepting is not publishing ------------------------------

  const text = n => [n.textContent, ...n.children.map(text)].join(' ');

  check('with no book chosen, the bar says so rather than naming one',
        els['bookbar-book'].textContent === 'No book chosen', els['bookbar-book'].textContent);

  await ctx.document.getElementById('go-console').onclick();
  await new Promise(r => setTimeout(r, 30));
  check('with no book chosen, the console asks which book first',
        !els['step-books'].classList.contains('hidden') &&
        !calls.includes('/api/console/load'), calls);
  check('it offers the books the server says the author can change',
        els['books-list'].children.length === 1 &&
        text(els['books-list']).includes('example-org/book-a'),
        text(els['books-list']));
  check('it says how many other books were left out, and why',
        els['books-hidden'].textContent.includes('2 other books') &&
        els['books-hidden'].textContent.includes('can’t make changes'),
        els['books-hidden'].textContent);

  await els['books-list'].children[0].children[0].onclick();
  await new Promise(r => setTimeout(r, 30));
  check('choosing a book sends its name, and nothing else decides it',
        bodies['/api/books/choose'] && bodies['/api/books/choose'].slug === 'book-a',
        bodies['/api/books/choose']);
  check('the bar then names the book and where it lives',
        text(els['bookbar-book']).includes('Book A') &&
        text(els['bookbar-book']).includes('example-org/book-a'),
        text(els['bookbar-book']));
  check('the bar says when the list of books is not fresh',
        els['bookbar-registry'].textContent.includes('as of 16 September 2026') &&
        !els['bookbar-registry'].classList.contains('hidden'),
        els['bookbar-registry'].textContent);
  check('the console says whose list it is',
        els['console-book'].textContent === 'For Book A', els['console-book'].textContent);
  check('the list is asked for by book',
        bodies['/api/console/load'] && bodies['/api/console/load'].book === 'book-a',
        bodies['/api/console/load']);
  check('the history link is the book’s own',
        els['link-history'].href === BOOK_A.history_url, els['link-history'].href);
  check('a book with no site shows no discussion link rather than a wrong one',
        els['discussion-item'].classList.contains('hidden'), 'shown');
  check('the console shows what is waiting',
        !els['step-console'].classList.contains('hidden'), 'wrong screen');
  check('what has been accepted but not sent is shown under "Going live"',
        !els['publish-block'].classList.contains('hidden'),
        'the going-live block was hidden');
  check('it counts as waiting on the author, so the tab is not shown as empty',
        els['waiting-count'].textContent === '1' &&
        els['console-empty'].classList.contains('hidden'),
        els['waiting-count'].textContent);

  await els['publish-list'].children[0].children[0].onclick();
  check('opening it shows what would go to readers',
        !els['step-publish'].classList.contains('hidden'), 'wrong screen');
  // Everything written onto the screen, cards and their lines alike.
  const words = n => [n.textContent, ...n.children.map(words)].join(' ');
  const pubText = words(els['publish-body']);
  check('it says plainly that the whole drafts area goes, not one change',
        pubText.includes('whole of the drafts area'), pubText);
  check('it names the pages that would change',
        pubText.includes('chapter-03'), pubText);
  check('it names everyone whose work would go, including the browser editor',
        pubText.includes('Textbook CMS'), pubText);

  let refused = '';
  els['error-text'].textContent = '';
  await els['publish-go'].onclick();
  refused = els['error-text'].textContent;
  check('publishing is refused until the author ticks the box',
        refused.includes('tick the box') && !calls.includes('/api/console/publish'),
        refused);

  els['publish-confirm'].checked = true;
  await els['publish-go'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('the box names the book whose readers would get it',
        text(els['publish-body']).includes('readers of Book A'), text(els['publish-body']));
  check('ticking the box sends the drafts to the live book',
        calls.includes('/api/console/publish'), calls);
  check('and it says which book it is publishing',
        bodies['/api/console/publish'].book === 'book-a' &&
        bodies['/api/console/publish'].number === 77,
        bodies['/api/console/publish']);
  check('afterwards the author is told his vault is now behind',
        els['cdone-steps'].children.some(c => c.textContent.includes('vault does not know')),
        els['cdone-steps'].children.map(c => c.textContent));

  // --- the chosen book decides, not the vault (§8 step 2) --------------------

  CURRENT_WS = WS_A_VAULT;
  await els['console-refresh'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('with a vault open, changing book is offered with nothing to close first',
        !els['bookbar-change'].classList.contains('hidden') &&
        els['bookbar-change'].textContent === 'Change book' &&
        !els['bookbar-close'].classList.contains('hidden'), els['bookbar-change'].textContent);
  check('the bar names the vault without saying it decides the book',
        els['bookbar-vault'].textContent.includes('Vault A') &&
        !els['bookbar-vault'].textContent.includes('decides'),
        els['bookbar-vault'].textContent);

  const closesBefore = calls.filter(c => c === '/api/vault/close').length;
  await els['bookbar-change'].onclick();
  await new Promise(r => setTimeout(r, 30));
  check('changing book goes straight to the list, and closes nothing on the way',
        !calls.includes('/api/books/switch') &&
        calls.filter(c => c === '/api/vault/close').length === closesBefore &&
        !els['step-books'].classList.contains('hidden') &&
        els['books-list'].children.every(li => !li.children[0].disabled),
        calls.slice(-4));
  check('the list says choosing a different book closes the vault',
        !els['books-vault'].classList.contains('hidden') &&
        els['books-vault-text'].textContent.includes('Choosing a different book closes it'),
        els['books-vault-text'].textContent);

  // Another book: whatever was counted for the last one goes.
  await els['books-list'].children[els['books-list'].children.length - 1]
    .children[0].onclick();
  check('the list only offers the books the author can change, even unlocked',
        bodies['/api/books/choose'].slug === 'book-a', bodies['/api/books/choose']);
  CURRENT_WS = WS_B;
  els['waiting-count'].textContent = '1';
  els['waiting-count'].classList.remove('hidden');
  await ctx.refreshWorkspace();
  check('when the book changes, the old book’s count is cleared at once',
        els['waiting-count'].textContent === '' &&
        els['waiting-count'].classList.contains('hidden'),
        els['waiting-count'].textContent);
  await ctx.document.getElementById('go-console').onclick();
  await new Promise(r => setTimeout(r, 30));
  check('switching to another book shows that book, not the last one',
        els['console-book'].textContent === 'For Book B' &&
        bodies['/api/console/load'].book === 'book-b',
        [els['console-book'].textContent, bodies['/api/console/load']]);
  check('and its own discussion link',
        els['link-discussion'].href === BOOK_B.discussion_url &&
        !els['discussion-item'].classList.contains('hidden'),
        els['link-discussion'].href);
  check('nothing in the page names a book of its own',
        !/confused4now|textbookproject2026-alt\/textbook\b/.test(js), 'constant found');

  // --- the account ---------------------------------------------------------

  const ACCOUNT = { signed_in: true, login: 'dept-coordinator-test', name: 'Dept Coordinator' };
  const WS_A_READONLY = Object.assign({}, WS_A_VAULT, {
    book: Object.assign({}, BOOK_A, { access: 'read' }), account: ACCOUNT });
  CURRENT_WS = WS_A_READONLY;
  await ctx.refreshWorkspace();
  check('the bar names the signed-in account',
        text(els['bookbar-account']).includes('dept-coordinator-test') &&
        !els['bookbar-signout'].classList.contains('hidden') &&
        !els['bookbar-switch-account'].classList.contains('hidden'),
        text(els['bookbar-account']));
  const offer = els['bookbar-book'].children.find(c => c.textContent === 'Use a different account');
  check('where the account can only read the book, switching account is offered right there',
        text(els['bookbar-book']).includes('can read this book but not change it') && !!offer,
        text(els['bookbar-book']));

  // A sign-out the Keychain would not honour is reported, not hidden.
  signoutReply = { __refuse: 'The sign-in could not be taken out of this Mac\'s Keychain, so you are still signed in.' };
  els['error-text'].textContent = '';
  await els['bookbar-signout'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('a sign-out that did not clear the Keychain says the author is still signed in',
        els['error-text'].textContent.includes('still signed in') && SIGNED_IN === true,
        els['error-text'].textContent);

  // On the import screen, before converting.
  DRAFTS_STATUS = { available: false, why: 'no_access',
    message: 'Your account (dept-coordinator-test) can\'t make changes to “Book A”.' };
  CURRENT_WS = WS_A_VAULT;
  await els['import-another'].onclick();
  await new Promise(r => setTimeout(r, 20));
  check('on the import screen, an account without push rights is offered another account before converting',
        !els['import-account-actions'].classList.contains('hidden') &&
        els['import-switch-account'].textContent === 'Use a different account',
        els['import-switch-account'].textContent);

  signoutReply = { signed_in: false, was: 'dept-coordinator-test' };
  await els['import-switch-account'].onclick();
  await new Promise(r => setTimeout(r, 30));
  check('switching account signs out as a switch, and goes to sign-in',
        bodies['/api/console/signout'].switching === true &&
        !els['step-console-signin'].classList.contains('hidden'),
        bodies['/api/console/signout']);
  check('the sign-in screen says which account was signed out, and how to pick another on GitHub',
        !els['signin-switching'].classList.contains('hidden') &&
        els['signin-switching-text'].textContent.includes('dept-coordinator-test') &&
        els['signin-switching-text'].textContent.includes('Keychain'),
        els['signin-switching-text'].textContent);

  // Signing in as the other account brings the author back to the import.
  signinReply = { signed_in: true, who: 'Other Author', login: 'other-author', same_account: false };
  SIGNED_IN = true;
  DRAFTS_STATUS = { available: true, why: 'ok',
    message: 'Once it is converted you can send it to the drafts area of “Book A”.' };
  await els['signin-start'].onclick();
  await new Promise(r => setTimeout(r, 2200));
  check('once signed in as the other account, the author is back on the import screen',
        !els['step-import'].classList.contains('hidden') &&
        els['import-account-actions'].classList.contains('hidden'),
        steps.filter(x => !els[x].classList.contains('hidden')));

  // GitHub gave back the same account: the author is told, not left guessing.
  signoutReply = { signed_in: false, was: 'other-author' };
  await els['bookbar-switch-account'].onclick();
  await new Promise(r => setTimeout(r, 20));
  signinReply = { signed_in: true, who: 'Other Author', login: 'other-author', same_account: true };
  SIGNED_IN = true;
  els['error-text'].textContent = '';
  await els['signin-start'].onclick();
  await new Promise(r => setTimeout(r, 2200));
  check('if GitHub signs the same account in again, the author is told why',
        els['error-text'].textContent.includes('other-author again') &&
        els['error-text'].textContent.includes('browser'),
        els['error-text'].textContent);

  // Settings has the same two actions.
  await els['open-settings'].onclick();
  check('Settings names the account and offers to sign out or use another',
        els['settings-account'].textContent.includes('author') &&
        !els['settings-account-actions'].classList.contains('hidden'),
        els['settings-account'].textContent);
  signoutReply = { signed_in: false, was: 'author' };
  await els['settings-signout'].onclick();
  check('signing out from Settings says so there',
        els['settings-account'].textContent === 'Not signed in.' &&
        els['settings-account-actions'].classList.contains('hidden'),
        els['settings-account'].textContent);
  els['settings-close'].onclick();

  // --- where a new chapter goes -------------------------------------------

  IMPORT_READY = { ready: true, folder: '/va/chapters', folder_name: 'chapters', chapters_here: 3 };
  await els['pick-docx'].onclick();
  check('with a vault open, a Word import goes in the book’s chapters folder unless the author picks another',
        els['folder-chosen'].textContent.startsWith('/va/chapters') &&
        els['folder-chosen'].textContent.includes('3 chapters'),
        els['folder-chosen'].textContent);

  // --- a chapter in the drafts area, no folder open (§8 step 2) -------------

  const find = (node, id) => node.id === id ? node
    : (node.children || []).map(c => find(c, id)).find(Boolean) || null;
  const allText = n => [n.textContent || n.nodeText || '',
                        ...(n.children || []).map(allText)].join(' ');
  CURRENT_WS = WS_A;
  await ctx.refreshWorkspace();
  await ctx.document.getElementById('go-chapters').onclick();
  await els['pick-drafts'].onclick();
  check('the drafts area is opened for the chosen book, with no folder',
        bodies['/api/drafts/open'] && bodies['/api/drafts/open'].book === 'book-a' &&
        !els['step-chapter'].classList.contains('hidden') &&
        els['vault-summary'].textContent.includes('drafts area') &&
        els['vault-summary'].textContent.includes('example-org/book-a'),
        els['vault-summary'].textContent);
  await els['chapter-list'].children[0].children[0].onclick();
  check('choosing a chapter says which book it is for',
        bodies['/api/prepare'].book === 'book-a' &&
        bodies['/api/prepare'].chapter === 'chapters/Only.md' &&
        !els['step-options'].classList.contains('hidden'), bodies['/api/prepare']);
  await els['start-analysis'].onclick();
  await ctx.goPreview();
  check('the preview says the changes go to the drafts area, not into files',
        els['do-commit'].textContent === 'Send to drafts' &&
        els['commit-warn-folder'].classList.contains('hidden') &&
        !els['commit-warn-drafts'].classList.contains('hidden') &&
        els['preview-file'].textContent.includes('drafts area of Book A'),
        els['preview-file'].textContent);

  commitReplies = [{ moved: true, same: true, head: 'd2',
    message: 'Nothing was sent. Something else changed the drafts area. Your choices still stand.' }];
  els['error-text'].textContent = '';
  els['confirm-box'].checked = true;
  await els['do-commit'].onclick();
  check('sending says which book and which drafts it was shown',
        bodies['/api/commit'].book === 'book-a' && bodies['/api/commit'].head === 'd1',
        bodies['/api/commit']);
  check('if drafts moved, nothing was sent, and the same choices are offered again',
        els['error-text'].textContent.includes('Nothing was sent') &&
        !els['step-preview'].classList.contains('hidden') &&
        els['confirm-box'].checked === false, els['error-text'].textContent);

  commitReplies = [{ sent: true, written: ['chapters/Only.md', 'glossary.md'],
    counts: { references: 1, terms: 2, glossary: 1, expanded: 0 },
    glossary_added: ['morphogenesis'], changed_lines: 2, repo: 'example-org/book-a',
    branch: 'drafts', head: 'd3', url: 'https://example.invalid/c/d3' }];
  els['confirm-box'].checked = true;
  await els['do-commit'].onclick();
  check('the second send goes on top of what drafts holds now',
        bodies['/api/commit'].head === 'd2', bodies['/api/commit']);
  check('once sent, the author is told it is in the drafts area, not a file',
        !els['step-done'].classList.contains('hidden') &&
        allText(els['done-summary']).includes('Sent to the drafts area of Book A') &&
        !allText(els['done-summary']).includes('Obsidian'),
        allText(els['done-summary']));

  await els['chapter-list'].children[0].children[0].onclick();
  await els['start-analysis'].onclick();
  await ctx.goPreview();
  commitReplies = [{ moved: true, same: false, head: 'd4',
    message: 'Nothing was sent. Something else changed this chapter. Please go through it again.' }];
  const preparesBefore = calls.filter(c => c === '/api/prepare').length;
  els['confirm-box'].checked = true;
  await els['do-commit'].onclick();
  check('if the chapter itself changed on drafts, it is read again and gone through again',
        calls.filter(c => c === '/api/prepare').length === preparesBefore + 1 &&
        !els['step-options'].classList.contains('hidden') &&
        els['error-text'].textContent.includes('go through it again'),
        els['error-text'].textContent);

  await els['download-copy'].onclick();
  check('"Download a copy" says where the copy went and how much is in it',
        bodies['/api/drafts/download'].book === 'book-a' &&
        els['download-note'].textContent.includes('book-a (drafts, 2026-09-22)') &&
        els['download-note'].textContent.includes('12 files'),
        els['download-note'].textContent);

  // --- a reader's suggestion, made in the drafts area -------------------------

  CURRENT_WS = WS_A_VAULT;
  await ctx.refreshWorkspace();
  await ctx.openSuggestion({ number: 51, who: 'Ada', when: '2026-09-01T00:00:00Z',
    page: 'chapter-01', suggestion: '"The the words" should be "The words"', reasoning: '' });
  const planText = allText(els['sug-plan']);
  els['sug-apply'] = find(els['sug-plan'], 'sug-apply');
  els['sug-apply-vault'] = find(els['sug-plan'], 'sug-apply-vault');
  check('the suggestion offers to make the change in the drafts area, and says the reply links it',
        planText.includes('drafts area of Book A') && planText.includes('links to it') &&
        els['sug-apply'] && els['sug-apply'].checked, planText);
  check('with the book’s vault open, the vault is offered too, ticked while readers see Publish',
        els['sug-apply-vault'] && els['sug-apply-vault'].checked &&
        planText.includes('Vault A'), planText);

  acceptReplies = [{ moved: true,
    message: 'Nothing was changed, and the suggestion is still open.',
    plan: Object.assign({}, PLAN, { head: 'h2' }) }];
  els['error-text'].textContent = '';
  await els['sug-accept'].onclick();
  check('accepting sends both ticks and the drafts the plan was read at',
        bodies['/api/console/accept'].apply === true &&
        bodies['/api/console/accept'].apply_vault === true &&
        bodies['/api/console/accept'].head === 'h1', bodies['/api/console/accept']);
  check('if drafts moved, the author is told nothing changed and shown it again',
        els['error-text'].textContent.includes('still open') &&
        !els['step-suggestion'].classList.contains('hidden'), els['error-text'].textContent);
  els['sug-apply'] = find(els['sug-plan'], 'sug-apply');
  els['sug-apply-vault'] = find(els['sug-plan'], 'sug-apply-vault');
  acceptReplies = [{ done: true, url: 'https://example.invalid/c/x', steps: [
    'Line 3 was changed in the drafts area, as one change of its own.',
    'A thank-you was sent, with a link to the change.',
    'The suggestion was marked as dealt with.'] }];
  await els['sug-accept'].onclick();
  check('accepting again goes on the fresh offer',
        bodies['/api/console/accept'].head === 'h2' &&
        !els['step-console-done'].classList.contains('hidden'),
        bodies['/api/console/accept']);

  // --- a suggestion the tool can't make: the reader is not told it was ------

  planReply = { can_apply: false, line_no: null, before: '', after: '', head: 'h3',
    branch: 'staging', vault: null,
    reason: 'This suggestion is written as a comment rather than as an exact ' +
            'replacement, so the tool will not change the chapter itself.' };
  await ctx.openSuggestion({ number: 61, who: 'A Reader', when: '2026-09-23T00:00:00Z',
    page: 'chapter-1', suggestion: 'blaaaa', reasoning: '' });
  const handText = allText(els['sug-plan']);
  check('a suggestion for the author to make says the chapter will not be changed',
        handText.includes('by hand') && handText.includes('Nothing in the chapter is changed') &&
        !find(els['sug-plan'], 'sug-apply'), handText);
  els['sug-apply'] = null; els['sug-apply-vault'] = null;
  acceptReplies = [{ done: true, steps: [
    'A thank-you was sent, saying you will make the change by hand. The chapter itself was not changed — make the change under Chapters.',
    'The suggestion was marked as dealt with.'] }];
  await els['sug-accept'].onclick();
  check('accepting it asks for no change at all',
        bodies['/api/console/accept'].apply === false &&
        bodies['/api/console/accept'].apply_vault === false, bodies['/api/console/accept']);
  planReply = null;

  // --- the window and the app behind it are the same version -------------------

  await ctx.api('/api/env', {});
  check('every request carries the build the page was served from',
        lastHeaders['X-AA-Build'] === '1.0.0+b1', lastHeaders);
  wrongBuild = { status: 409, body: { stale: true,
    error: 'This window and the part of the app running in the background are from ' +
           'different versions, so nothing was done. Please quit and reopen the app.' } };
  let versionSaid = '';
  try { await ctx.api('/api/console/accept', {}); } catch (e) { versionSaid = e.message; }
  check('a refusal for being another version tells the author to quit and reopen',
        versionSaid.includes('Please quit and reopen the app.'), versionSaid);
  wrongBuild = { status: 404, body: { error: 'Unknown request.' } };
  versionSaid = '';
  try { await ctx.api('/api/console/accept', {}); } catch (e) { versionSaid = e.message; }
  check('so does an older app that has never heard of the request',
        versionSaid.includes('Please quit and reopen the app.'), versionSaid);
  wrongBuild = null;

  const failed = results.filter(r => !r[1]);
  console.log(`\n  ${results.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
