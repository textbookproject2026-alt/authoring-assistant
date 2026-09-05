/* Loads the real app.js against a stub browser and drives the review flow,
   to check the Yes / No / Yes-to-all logic and the progress counter. */
const fs = require('fs'), vm = require('vm'), path = require('path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'app/web/index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'app/web/app.js'), 'utf8');

const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);

function mkEl(id) {
  const e = {
    id, textContent: '', innerHTML: '', value: '', checked: false, disabled: false,
    style: {}, dataset: {}, children: [], parentElement: null,
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { on === undefined ? (this._s.has(c) ? this._s.delete(c) : this._s.add(c)) : (on ? this._s.add(c) : this._s.delete(c)); },
    },
    appendChild(c) { this.children.push(c); return c; },
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
               'step-console-done'];
steps.forEach(s => els[s].classList.add('step'));

const groupSpans = [mkEl('grp1'), mkEl('grp2')];
const secondary = mkEl('secondary'); secondary.style = {};
const warnbox = mkEl('warnbox');
const previewH2 = mkEl('previewh2');

const document = {
  body: { dataset: { token: 'T' } },
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

let lastPreviewBody = null;
let lastConvertBody = null;
// Flipped part-way through the run, to check the screen shown when the
// converter is missing and the one shown once it has been installed.
let IMPORT_READY = { ready: false, where: null, version: null, can_install: true };
const calls = [];
const fetch = async (route, opts) => {
  calls.push(route);
  const body = JSON.parse(opts.body || '{}');
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
      media_dir: 'Chapter 6-media',
      media: [{ rel: 'Chapter 6-media/rId1.png', name: 'rId1.png', ext: 'png', size: 2048 }],
      counts: { lines: 3, words: 4, pictures: 1, headings: 1,
                pipe_tables: 0, html_tables: 1, footnotes: 2 },
      notes: [
        { level: 'ok', headline: '1 picture was taken out of the Word file',
          body: 'It is in a folder called "Chapter 6-media".', check: 'Look at it.' },
        { level: 'warn', headline: '1 table could not be made into a proper table',
          body: 'Its cells were merged.', check: 'Unmerge them in Word.' },
      ],
    }; })(),
    '/api/import/save': {
      chapter: '/v/Chapters/Chapter 6.md', chapter_name: 'Chapter 6.md',
      media: '/v/Chapters/Chapter 6-media', media_name: 'Chapter 6-media',
      folder: '/v/Chapters',
    },
    '/api/import/cancel': { ok: true },

    '/api/console/status': { configured: true, signed_in: true, who: 'The Author',
                             keychain: true, root: null, root_name: null },
    '/api/console/load': {
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
    '/api/prepare': {
      chapter: '/v/Chapters/Chapter 6.md', chapter_name: 'Chapter 6.md',
      warnings: [], blockers: [], hard_wrapped: false, lines: 3,
      has_references: true, concept_pages: ['Emergence'],
      concept_source: 'Definitions', deepseek: false,
    },
    '/api/preview': (() => { lastPreviewBody = body; return {
      counts: { references: 1, terms: 2, glossary: 1, expanded: 1 },
      diff: [{ line_no: 5, before: 'a', after: 'b' }],
      new_text: 'whole file', glossary_path: '/v/glossary.md', glossary_exists: false,
      glossary_added: ['morphogenesis'], glossary_after: '# Glossary\n\n## morphogenesis\n\nStructural elaboration.\n',
      changed_lines: [4],
    }; })(),
  }[route] || {};
  return { ok: true, json: async () => reply };
};

const listeners = {};
let beacons = 0;
const ctx = {
  document, fetch, console, setInterval, clearInterval, setTimeout,
  encodeURIComponent,
  navigator: { sendBeacon: () => { beacons++; return true; } },
  window: {
    scrollTo() {},
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
        els['import-media-note'].textContent.includes('Chapter 6-media'),
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

  await els['import-analyse'].onclick();
  await new Promise(r => setTimeout(r, 30));
  check('the new chapter can go straight into the three analyses',
        calls.includes('/api/open') && calls.includes('/api/prepare'), calls);
  check('and it lands on the options screen, ready to be looked through',
        !els['step-options'].classList.contains('hidden'), 'wrong screen');

  // --- the console: accepting is not publishing ------------------------------

  await ctx.document.getElementById('go-console').onclick();
  await new Promise(r => setTimeout(r, 30));
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
  check('ticking the box sends the drafts to the live book',
        calls.includes('/api/console/publish'), calls);
  check('afterwards the author is told his vault is now behind',
        els['cdone-steps'].children.some(c => c.textContent.includes('vault does not know')),
        els['cdone-steps'].children.map(c => c.textContent));

  const failed = results.filter(r => !r[1]);
  console.log(`\n  ${results.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
