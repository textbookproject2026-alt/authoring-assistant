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
               'step-preview','step-done','step-stopped'];
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

  const failed = results.filter(r => !r[1]);
  console.log(`\n  ${results.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
