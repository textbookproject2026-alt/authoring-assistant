/* The interface. One question at a time, plain words, nothing written until the
   author has seen the finished file and said yes. */

const TOKEN = document.body.dataset.token;

const S = {
  sessionId: null,
  root: null,
  chapters: [],
  chapter: null,
  findings: [],
  index: 0,
  decisions: {},        // finding id -> true / false
  groupRule: {},        // group -> "yes" / "no"
  expandGroups: [],     // concept pages the author said yes-to-all for
  notes: [],
  preview: null,
};

/* ---------- plumbing ---------- */

async function api(route, body) {
  const res = await fetch(route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-AA-Token': TOKEN },
    body: JSON.stringify(body || {}),
  });
  let data;
  try { data = await res.json(); } catch (e) {
    throw new Error('The tool stopped responding. Please close this tab and start it again.');
  }
  if (!res.ok || data.error) throw new Error(data.error || 'Something went wrong.');
  return data;
}

function show(id) {
  document.querySelectorAll('.step').forEach(s => s.classList.add('hidden'));
  document.getElementById(id).classList.remove('hidden');
  window.scrollTo(0, 0);
}

function fail(message) {
  document.getElementById('error-text').textContent = message;
  document.getElementById('error').classList.remove('hidden');
}
document.getElementById('error-ok').onclick = () =>
  document.getElementById('error').classList.add('hidden');

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

document.querySelectorAll('[data-back]').forEach(b => {
  b.onclick = () => show(b.dataset.back);
});

/* ---------- step 1: choosing ---------- */

async function pick(kind) {
  const note = document.getElementById('choose-note');
  note.textContent = 'The chooser window is open — it may be behind this one.';
  try {
    const picked = await api('/api/pick', { kind });
    note.textContent = '';
    if (picked.cancelled) return;
    if (picked.error) return fail(picked.error);
    await openTarget(picked.path);
  } catch (e) {
    note.textContent = '';
    fail(e.message);
  }
}

document.getElementById('pick-file').onclick = () => pick('file');
document.getElementById('pick-folder').onclick = () => pick('folder');

async function openTarget(path) {
  const data = await api('/api/open', { path });
  S.sessionId = data.session_id;
  S.root = data.root;
  S.chapters = data.chapters;

  if (data.mode === 'file') {
    return chooseChapter(data.chapters[0].path);
  }
  document.getElementById('vault-summary').textContent =
    `${data.chapters.length} chapters in ${data.root_name}`;
  renderChapters('');
  show('step-chapter');
}

function renderChapters(filter) {
  const list = document.getElementById('chapter-list');
  list.innerHTML = '';
  const needle = filter.trim().toLowerCase();
  const shown = S.chapters.filter(c => !needle || c.rel.toLowerCase().includes(needle));
  if (!shown.length) {
    list.appendChild(el('li', '', 'No chapters match what you typed.'));
    return;
  }
  shown.forEach(c => {
    const li = el('li');
    const b = el('button');
    b.appendChild(el('span', '', c.name));
    if (c.folder) b.appendChild(el('span', 'folder', c.folder));
    b.onclick = () => chooseChapter(c.path);
    li.appendChild(b);
    list.appendChild(li);
  });
}
document.getElementById('chapter-filter').oninput = e => renderChapters(e.target.value);

/* ---------- step 2: checks and options ---------- */

async function chooseChapter(path) {
  try {
    const info = await api('/api/prepare', { session_id: S.sessionId, chapter: path });
    S.chapter = info;
    renderPreflight(info);
    show('step-options');
  } catch (e) { fail(e.message); }
}

function renderPreflight(info) {
  const box = document.getElementById('preflight');
  box.innerHTML = '';

  const head = el('p');
  head.innerHTML = `You are working on <strong>${escapeHtml(info.chapter_name)}</strong>.`;
  box.appendChild(head);

  info.blockers.forEach(t => box.appendChild(el('div', 'blocker', t)));
  info.warnings.forEach(t => box.appendChild(el('div', 'warning', t)));

  if (!info.has_references) {
    box.appendChild(el('div', 'warning',
      'This chapter has no References section, so citations cannot be linked to ' +
      'anything. The other checks still work.'));
  }
  if (!info.concept_pages.length) {
    box.appendChild(el('div', 'warning',
      'No concept pages were found near this chapter, so there are no terms to ' +
      'link to. The other checks still work.'));
  } else {
    box.appendChild(el('div', 'notice ok',
      `Concept pages will be taken from “${info.concept_source}” ` +
      `(${info.concept_pages.length}: ${info.concept_pages.slice(0, 6).join(', ')}` +
      `${info.concept_pages.length > 6 ? ', and more' : ''}).`));
  }

  ENV.deepseek = info.deepseek;
  refreshDeepseekOption();

  const blocked = info.blockers.length > 0;
  document.getElementById('start-analysis').disabled = blocked;
}

document.getElementById('start-analysis').onclick = async () => {
  const analyses = [];
  if (document.getElementById('opt-references').checked) analyses.push('references');
  if (document.getElementById('opt-terms').checked) analyses.push('terms');
  if (document.getElementById('opt-glossary').checked) analyses.push('glossary');
  if (!analyses.length) return fail('Please tick at least one thing to look for.');

  show('step-working');
  const useDeepseek = document.getElementById('opt-deepseek').checked;
  document.getElementById('working-note').textContent = useDeepseek
    ? 'Reading the chapter, and asking DeepSeek for glossary ideas. This can take up to a minute.'
    : 'This usually takes a moment.';

  try {
    const data = await api('/api/analyse', {
      session_id: S.sessionId,
      analyses,
      first_mention_only: document.getElementById('opt-first').checked,
      use_deepseek: useDeepseek,
      anchor_style: document.querySelector('input[name=anchor]:checked').value,
    });
    S.findings = data.findings;
    S.notes = data.notes;
    S.index = 0;
    S.decisions = {};
    S.groupRule = {};
    S.expandGroups = [];
    if (!S.findings.length) return goPreview();
    renderFinding();
    show('step-review');
  } catch (e) { fail(e.message); show('step-options'); }
};

/* ---------- step 3: one finding at a time ---------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderFinding() {
  const f = S.findings[S.index];
  const total = S.findings.length;

  document.getElementById('progress-label').textContent =
    `${S.index + 1} of ${total}`;
  document.getElementById('progress-bar').style.width =
    `${((S.index) / total) * 100}%`;

  document.getElementById('finding-title').textContent = f.title;
  let explain = f.explain;
  if (f.occurrence_total > 1) {
    explain += ` This is mention ${f.occurrence} of ${f.occurrence_total} in the chapter.`;
  }
  document.getElementById('finding-explain').textContent = explain;

  const lineSpan = document.getElementById('finding-line');
  lineSpan.textContent = f.line_no || '—';
  lineSpan.parentElement.style.display = f.line_no ? '' : 'none';

  document.getElementById('sentence-before').innerHTML =
    escapeHtml(f.before) + '<mark>' + escapeHtml(f.match) + '</mark>' + escapeHtml(f.after);

  const afterCard = document.getElementById('after-card');
  if (f.kind === 'glossary') {
    afterCard.classList.add('hidden');
  } else {
    afterCard.classList.remove('hidden');
    document.getElementById('sentence-after').innerHTML =
      escapeHtml(f.before) + '<mark class="new">' + escapeHtml(f.becomes) + '</mark>' +
      escapeHtml(f.after);
  }

  const detailCard = document.getElementById('detail-card');
  if (f.detail) {
    detailCard.classList.remove('hidden');
    document.getElementById('detail-label').textContent = f.detail_label || '';
    document.getElementById('detail-text').textContent = f.detail;
  } else {
    detailCard.classList.add('hidden');
  }

  const label = f.kind === 'glossary'
    ? `“${f.group_label}”`
    : (f.kind === 'term' ? `“${f.group_label}”` : `${f.group_label}`);
  document.querySelectorAll('.grp').forEach(s => s.textContent = label);

  const many = f.occurrence_total > 1 || f.kind === 'term';
  document.querySelector('.answers.secondary').style.display = many ? '' : 'none';

  document.getElementById('ans-back').style.display = S.index > 0 ? '' : 'none';
}

function answer(value, all) {
  const f = S.findings[S.index];
  if (all) {
    S.groupRule[f.group] = value ? 'yes' : 'no';
    S.findings.forEach(g => { if (g.group === f.group) S.decisions[g.id] = value; });
    if (value && f.kind === 'term') {
      if (!S.expandGroups.includes(f.group_label)) S.expandGroups.push(f.group_label);
    }
    if (!value && f.kind === 'term') {
      S.expandGroups = S.expandGroups.filter(t => t !== f.group_label);
    }
  } else {
    S.decisions[f.id] = value;
  }
  advance();
}

function advance() {
  let i = S.index + 1;
  // Skip anything already settled by a "yes to all" or "no to all".
  while (i < S.findings.length && S.groupRule[S.findings[i].group]) i++;
  if (i >= S.findings.length) return goPreview();
  S.index = i;
  renderFinding();
}

document.getElementById('ans-yes').onclick = () => answer(true, false);
document.getElementById('ans-no').onclick = () => answer(false, false);
document.getElementById('ans-yes-all').onclick = () => answer(true, true);
document.getElementById('ans-no-all').onclick = () => answer(false, true);
document.getElementById('ans-stop').onclick = () => goPreview();
document.getElementById('ans-back').onclick = () => {
  let i = S.index - 1;
  while (i > 0 && S.groupRule[S.findings[i].group]) i--;
  if (i < 0) return;
  S.index = i;
  renderFinding();
};

/* ---------- step 4: preview ---------- */

async function goPreview() {
  const accepted = Object.keys(S.decisions).filter(id => S.decisions[id]);
  show('step-working');
  document.getElementById('working-note').textContent = 'Putting your chapter together…';
  try {
    const p = await api('/api/preview', {
      session_id: S.sessionId,
      accepted,
      expand_groups: S.expandGroups,
    });
    S.preview = p;
    renderPreview(p, accepted.length);
    show('step-preview');
  } catch (e) { fail(e.message); show('step-review'); }
}

function renderPreview(p, acceptedCount) {
  document.querySelector('#step-preview h2').textContent = S.findings.length
    ? 'Here is exactly what will change'
    : 'Nothing needs changing';
  const c = p.counts;
  const bits = [];
  if (c.references) bits.push(`${c.references} citation${c.references === 1 ? '' : 's'} linked`);
  if (c.terms) bits.push(`${c.terms} mention${c.terms === 1 ? '' : 's'} linked to concept pages`);
  if (c.expanded) bits.push(`every mention of ${c.expanded} term${c.expanded === 1 ? '' : 's'} linked`);
  if (c.glossary) bits.push(`${c.glossary} glossary entr${c.glossary === 1 ? 'y' : 'ies'} added`);

  const nothing = !p.diff.length && !p.glossary_added.length;
  document.getElementById('nothing-text').textContent = S.findings.length
    ? 'You did not choose any changes, so there is nothing to save.'
    : 'I read the whole chapter and found nothing that needs changing. '
      + 'Everything that could be linked is already linked.';
  document.getElementById('nothing-chosen').classList.toggle('hidden', !nothing);
  document.getElementById('preview-body').classList.toggle('hidden', nothing);
  document.querySelector('.warnbox').classList.toggle('hidden', nothing);

  document.getElementById('preview-summary').textContent = nothing
    ? ''
    : `You chose: ${bits.join(', ')}. ` +
      `${p.diff.length} line${p.diff.length === 1 ? '' : 's'} of your chapter will change. ` +
      `Every other line stays exactly as it is.`;

  document.getElementById('preview-file').textContent =
    S.chapter ? S.chapter.chapter : '';

  const changes = document.getElementById('tab-changes');
  changes.innerHTML = '';
  if (!p.diff.length) {
    changes.appendChild(el('div', 'dline', 'No lines of the chapter itself will change.'));
  }
  p.diff.forEach(d => {
    const box = el('div', 'dline');
    box.appendChild(el('div', 'n', `Line ${d.line_no}`));
    box.appendChild(el('div', 'was', d.before));
    box.appendChild(el('div', 'now', d.after));
    changes.appendChild(box);
  });
  document.getElementById('whole-file').textContent = p.new_text;

  const gBlock = document.getElementById('glossary-block');
  if (p.glossary_added.length) {
    gBlock.classList.remove('hidden');
    document.getElementById('glossary-file').textContent =
      p.glossary_path + (p.glossary_exists ? '' : '  (this file will be created)');
    const gp = document.getElementById('glossary-preview');
    gp.innerHTML = '';
    const after = p.glossary_after.split('\n');
    p.glossary_added.forEach(term => {
      const i = after.findIndex(l => l.trim() === `## ${term}`);
      const def = i >= 0 ? (after.slice(i + 1, i + 4).find(l => l.trim()) || '') : '';
      const entry = el('div', 'gentry');
      entry.appendChild(el('div', 'gterm', term));
      entry.appendChild(el('div', '', def.trim()));
      gp.appendChild(entry);
    });
  } else {
    gBlock.classList.add('hidden');
  }

  const notesBlock = document.getElementById('notes-block');
  const notesList = document.getElementById('notes-list');
  notesList.innerHTML = '';
  S.notes.forEach(n => notesList.appendChild(el('li', '', n)));
  notesBlock.classList.toggle('hidden', !S.notes.length);

  document.getElementById('confirm-box').checked = false;
  document.getElementById('do-commit').disabled = true;
}

document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('tab-changes').classList.toggle('hidden', t.dataset.tab !== 'changes');
    document.getElementById('tab-whole').classList.toggle('hidden', t.dataset.tab !== 'whole');
  };
});

document.getElementById('confirm-box').onchange = e => {
  document.getElementById('do-commit').disabled = !e.target.checked;
};

document.getElementById('preview-back').onclick = () => {
  if (!S.findings.length) return show('step-options');
  S.index = 0;
  while (S.index < S.findings.length && S.groupRule[S.findings[S.index].group]) S.index++;
  if (S.index >= S.findings.length) S.index = 0;
  renderFinding();
  show('step-review');
};

/* ---------- step 5: saving ---------- */

document.getElementById('do-commit').onclick = async () => {
  const accepted = Object.keys(S.decisions).filter(id => S.decisions[id]);
  document.getElementById('do-commit').disabled = true;
  show('step-working');
  document.getElementById('working-note').textContent = 'Saving…';
  try {
    const r = await api('/api/commit', {
      session_id: S.sessionId,
      accepted,
      expand_groups: S.expandGroups,
    });
    renderDone(r);
    show('step-done');
  } catch (e) {
    fail(e.message);
    show('step-preview');
    document.getElementById('confirm-box').checked = false;
  }
};

function renderDone(r) {
  const box = document.getElementById('done-summary');
  box.innerHTML = '';
  const list = el('ul', 'summary-list');

  if (r.changed_lines) {
    list.appendChild(el('li',
      '', `${r.changed_lines} line${r.changed_lines === 1 ? '' : 's'} of your chapter were changed. Everything else was left exactly as it was.`));
  }
  if (r.counts.references) list.appendChild(el('li', '', `${r.counts.references} citations are now linked to your reference list.`));
  if (r.counts.terms) list.appendChild(el('li', '', `${r.counts.terms} mentions now link to your concept pages.`));
  if (r.glossary_added.length) list.appendChild(el('li', '', `Added to your glossary: ${r.glossary_added.join(', ')}.`));
  if (!r.written.length) list.appendChild(el('li', '', 'Nothing needed changing, so no files were touched.'));

  box.appendChild(list);
  if (r.written.length) {
    const files = el('p', 'quiet', 'Files written: ' + r.written.join('  •  '));
    box.appendChild(files);
    box.appendChild(el('p', '', 'If Obsidian is open, it will pick up the changes on its own.'));
  }
}

document.getElementById('again').onclick = () => {
  S.findings = []; S.decisions = {}; S.groupRule = {}; S.expandGroups = [];
  if (S.chapters.length > 1) { renderChapters(''); show('step-chapter'); }
  else show('step-choose');
};

document.getElementById('finish').onclick = async () => {
  try { await api('/api/quit', {}); } catch (e) { /* the server is already going */ }
  show('step-stopped');
};

/* ---------- settings ---------- */

let ENV = {};

async function loadEnv() {
  try { ENV = await api('/api/env', {}); } catch (e) { ENV = {}; }
  return ENV;
}

function renderSettings() {
  const present = !!ENV.deepseek;
  document.getElementById('key-present').classList.toggle('hidden', !present);
  if (present) document.getElementById('key-hint').textContent = '…' + (ENV.deepseek_hint || '');
  document.getElementById('key-file').textContent = ENV.key_store || "this Mac's Keychain";
  document.getElementById('key-input').value = '';
  setKeyMessage('', '');

  const cfg = C.status || {};
  const hasClient = !!cfg.configured;
  document.getElementById('client-present').classList.toggle('hidden', !hasClient);
  if (hasClient) document.getElementById('client-hint').textContent = cfg.client_hint + '…';
  document.getElementById('client-input').value = '';
  setClientMessage('', '');

  const list = document.getElementById('env-list');
  list.innerHTML = '';
  const rows = [
    ['Version', ENV.version || 'unknown'],
    ['pandoc', ENV.pandoc
      ? 'installed — not needed by this tool, but handy if you also export to Word or PDF'
      : 'not installed — that is fine, this tool does not need it'],
    ['Obsidian', ENV.obsidian_running ? 'open right now' : 'not open'],
    ['Files kept in', ENV.support_dir || ''],
  ];
  rows.forEach(([k, v]) => {
    const li = el('li');
    li.appendChild(el('strong', '', k + ': '));
    li.appendChild(document.createTextNode(v));
    list.appendChild(li);
  });
}

function setKeyMessage(text, kind) {
  const n = document.getElementById('key-message');
  n.textContent = text;
  n.className = 'quiet' + (kind ? ' ' + kind : '');
}

function setClientMessage(text, kind) {
  const n = document.getElementById('client-message');
  n.textContent = text;
  n.className = 'quiet' + (kind ? ' ' + kind : '');
}

async function openSettings() {
  await loadEnv();
  try { C.status = await api('/api/console/status', {}); } catch (e) { /* shown as absent */ }
  renderSettings();
  document.getElementById('settings').classList.remove('hidden');
}

document.getElementById('open-settings').onclick = openSettings;
document.getElementById('settings-close').onclick = () => {
  document.getElementById('settings').classList.add('hidden');
  refreshDeepseekOption();
};

document.getElementById('client-save').onclick = async () => {
  const value = document.getElementById('client-input').value.trim();
  if (!value) return setClientMessage('Please paste the identifier first.', 'bad');
  setClientMessage('Saving…', '');
  try {
    await api('/api/console/save-client', { client_id: value });
    C.status = await api('/api/console/status', {});
    setClientMessage('Saved. You can now sign in from "Waiting for you".', 'ok');
    document.getElementById('client-present').classList.remove('hidden');
    document.getElementById('client-hint').textContent = C.status.client_hint + '…';
    document.getElementById('client-input').value = '';
  } catch (e) {
    setClientMessage(e.message, 'bad');
  }
};

document.getElementById('key-save').onclick = async () => {
  const key = document.getElementById('key-input').value.trim();
  if (!key) return setKeyMessage('Please paste a key first.', 'bad');
  setKeyMessage('Saving…', '');
  try {
    const r = await api('/api/save-key', { key });
    ENV.deepseek = true; ENV.deepseek_hint = r.hint;
    renderSettings();
    setKeyMessage('Saved. Checking it works…', '');
    const t = await api('/api/test-key', {});
    setKeyMessage(t.message, t.ok ? 'good' : 'bad');
  } catch (e) { setKeyMessage(e.message, 'bad'); }
};

document.getElementById('key-test').onclick = async () => {
  const typed = document.getElementById('key-input').value.trim();
  setKeyMessage('Checking…', '');
  try {
    const t = await api('/api/test-key', typed ? { key: typed } : {});
    setKeyMessage(t.message, t.ok ? 'good' : 'bad');
  } catch (e) { setKeyMessage(e.message, 'bad'); }
};

document.getElementById('key-clear').onclick = async () => {
  try {
    await api('/api/clear-key', {});
    ENV.deepseek = false; ENV.deepseek_hint = null;
    renderSettings();
    setKeyMessage('The saved key has been removed from this Mac.', '');
  } catch (e) { setKeyMessage(e.message, 'bad'); }
};

function refreshDeepseekOption() {
  const box = document.getElementById('opt-deepseek');
  const state = document.getElementById('deepseek-state');
  if (!box) return;
  if (ENV.deepseek) {
    box.disabled = false;
    state.textContent = 'A key is set up on this Mac.';
  } else {
    box.disabled = true; box.checked = false;
    state.textContent = 'No key is set up, so this is turned off. ' +
      'You can add one in Settings. The plain checks work perfectly well without it.';
  }
}

/* ---------- quitting cleanly ---------- */

document.getElementById('quit-app').onclick = async () => {
  try { await api('/api/quit', {}); } catch (e) { /* already going */ }
  stopHeartbeat();
  show('step-stopped');
};

let heartbeat = null;
function startHeartbeat() {
  if (heartbeat) return;
  const beat = () => fetch('/api/ping?t=' + encodeURIComponent(TOKEN), {
    method: 'POST', headers: { 'X-AA-Token': TOKEN }, body: '{}',
  }).catch(() => {});
  beat();
  heartbeat = setInterval(beat, 5000);
}
function stopHeartbeat() {
  if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
}

// Closing the tab, or quitting the browser, tells the tool to stop. A reload
// sends the same message, which is why the tool waits a few seconds first.
window.addEventListener('pagehide', () => {
  try {
    navigator.sendBeacon('/api/bye?t=' + encodeURIComponent(TOKEN), '{}');
  } catch (e) { /* nothing more we can do from a closing page */ }
});


/* ---------- the console ---------- */
/* The second half of the app: what readers and contributors have sent, shown
   without any of the vocabulary of the service it comes from. */

const SITE = 'https://bptext2026.xyz';
const DISCUSSION_URL = 'https://hypothes.is/search?q=url:' + SITE + '/*';
const HISTORY_URL = 'https://github.com/textbookproject2026-alt/textbook/commits/main';

const C = {
  status: null,
  data: null,
  suggestion: null,
  plan: null,
  draft: null,
  poll: null,
};

function place(which) {
  document.getElementById('go-chapters').classList.toggle('active', which === 'chapters');
  document.getElementById('go-console').classList.toggle('active', which === 'console');
}

function when(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

function stopPolling() {
  if (C.poll) { clearTimeout(C.poll); C.poll = null; }
}

/* --- getting in --- */

async function enterConsole() {
  place('console');
  stopPolling();
  try {
    C.status = await api('/api/console/status', {});
  } catch (e) { return fail(e.message); }

  if (!C.status.configured) return show('step-console-setup');
  if (!C.status.signed_in) {
    document.getElementById('signin-code-box').classList.add('hidden');
    return show('step-console-signin');
  }
  await loadConsole();
}

document.getElementById('go-console').onclick = enterConsole;
document.getElementById('go-chapters').onclick = () => {
  place('chapters');
  stopPolling();
  show(S.sessionId ? 'step-choose' : 'step-choose');
};
document.getElementById('setup-back').onclick = () => document.getElementById('go-chapters').click();
document.getElementById('signin-back').onclick = () => document.getElementById('go-chapters').click();
document.getElementById('setup-open-settings').onclick = openSettings;

/* --- signing in --- */

document.getElementById('signin-start').onclick = async () => {
  const box = document.getElementById('signin-code-box');
  const btn = document.getElementById('signin-start');
  btn.disabled = true;
  try {
    const r = await api('/api/console/signin-start', {});
    document.getElementById('signin-code').textContent = r.code;
    document.getElementById('signin-url').textContent =
      'If the page did not open, go to ' + r.url + ' yourself.';
    document.getElementById('signin-waiting').textContent =
      'Waiting for you to approve… this code lasts about ' + r.minutes + ' minutes.';
    box.classList.remove('hidden');
    window.open(r.url, '_blank', 'noopener');
    pollSignin(2);
  } catch (e) {
    btn.disabled = false;
    fail(e.message);
  }
};

function pollSignin(wait) {
  stopPolling();
  C.poll = setTimeout(async () => {
    try {
      const r = await api('/api/console/signin-poll', {});
      if (r.waiting) return pollSignin(r.wait || 5);
      document.getElementById('signin-start').disabled = false;
      C.status = null;
      await enterConsole();
    } catch (e) {
      document.getElementById('signin-start').disabled = false;
      document.getElementById('signin-code-box').classList.add('hidden');
      fail(e.message);
    }
  }, Math.max(2, wait) * 1000);
}

document.getElementById('console-signout').onclick = async () => {
  try { await api('/api/console/signout', {}); } catch (e) { /* signing out always succeeds locally */ }
  C.status = null; C.data = null;
  setWaitingCount(0);
  await enterConsole();
};

/* --- the list --- */

function setWaitingCount(n) {
  const pill = document.getElementById('waiting-count');
  pill.textContent = n ? String(n) : '';
  pill.classList.toggle('hidden', !n);
}

async function loadConsole() {
  show('step-console');
  document.getElementById('console-who').textContent = 'Checking…';
  try {
    C.data = await api('/api/console/load', {});
  } catch (e) {
    return fail(e.message);
  }
  renderConsole();
}

document.getElementById('console-refresh').onclick = loadConsole;

document.getElementById('console-vault').onclick = async () => {
  try {
    const r = await api('/api/console/pick-vault', {});
    if (r.cancelled) return;
    if (r.error) return fail(r.error);
    C.status.root = r.root; C.status.root_name = r.root_name;
    renderConsole();
  } catch (e) { fail(e.message); }
};

function renderConsole() {
  const d = C.data || {};
  document.getElementById('console-who').textContent =
    d.who ? ('Signed in as ' + d.who) : 'Signed in';

  document.getElementById('link-discussion').href = DISCUSSION_URL;
  document.getElementById('link-history').href = HISTORY_URL;

  const vaultBtn = document.getElementById('console-vault');
  vaultBtn.textContent = C.status && C.status.root_name
    ? ('Chapters folder: ' + C.status.root_name + ' — change')
    : 'Choose my chapters folder';

  // Anything that went wrong is said out loud rather than left as a blank list.
  const probs = document.getElementById('console-problems');
  probs.innerHTML = '';
  if ((d.problems || []).length) {
    probs.classList.remove('hidden');
    const p = el('p', 'notice' + (d.offline ? '' : ' bad'));
    p.textContent = d.offline
      ? 'This Mac is not online, so this list may be incomplete. Nothing can be accepted or declined until it is back.'
      : d.problems.join('  ');
    probs.appendChild(p);
  } else {
    probs.classList.add('hidden');
  }

  const sugs = d.suggestions || [];
  const drafts = d.drafts || [];
  setWaitingCount(sugs.length + drafts.length);
  document.getElementById('console-empty').classList.toggle(
    'hidden', !!(sugs.length || drafts.length || (d.problems || []).length));

  document.getElementById('suggestions-count').textContent =
    sugs.length ? '(' + sugs.length + ')' : '(none)';
  document.getElementById('drafts-count').textContent =
    drafts.length ? '(' + drafts.length + ')' : '(none)';

  const sl = document.getElementById('suggestions-list');
  sl.innerHTML = '';
  sugs.forEach(s => {
    const li = el('li');
    const b = el('button');
    b.appendChild(el('strong', '', s.page));
    b.appendChild(el('span', 'who', s.who + ' · ' + when(s.when)));
    b.appendChild(el('span', 'snip', (s.suggestion || '').slice(0, 140)));
    b.onclick = () => openSuggestion(s);
    li.appendChild(b);
    sl.appendChild(li);
  });
  if (!sugs.length) sl.appendChild(el('li', 'none', 'Nothing waiting.'));

  const dl = document.getElementById('drafts-list');
  dl.innerHTML = '';
  drafts.forEach(x => {
    const li = el('li');
    const b = el('button');
    b.appendChild(el('strong', '', x.title));
    b.appendChild(el('span', 'who', 'by ' + x.who + ' · ' + when(x.when)));
    b.onclick = () => openDraft(x);
    li.appendChild(b);
    dl.appendChild(li);
  });
  if (!drafts.length) dl.appendChild(el('li', 'none', 'Nothing waiting.'));

  const wl = document.getElementById('weekly-list');
  wl.innerHTML = '';
  (d.weekly || []).forEach(w => {
    const li = el('li');
    li.appendChild(el('strong', '', w.name + ': '));
    const state = w.state === 'ok' ? 'ran successfully'
      : w.state === 'failed' ? 'did not finish — worth telling Alec'
      : 'has not run yet';
    li.appendChild(document.createTextNode(state + (w.when ? ' (' + when(w.when) + ')' : '')));
    wl.appendChild(li);
  });
  if (!(d.weekly || []).length) wl.appendChild(el('li', '', 'Could not be checked.'));
}

/* --- one suggestion --- */

async function openSuggestion(s) {
  C.suggestion = s; C.plan = null;
  show('step-suggestion');
  document.getElementById('sug-meta').textContent =
    'Sent by ' + s.who + ' on ' + when(s.when) + ' about ' + s.page;
  document.getElementById('sug-text').textContent = s.suggestion || '(they left no text)';
  const rc = document.getElementById('sug-reason-card');
  rc.classList.toggle('hidden', !s.reasoning);
  if (s.reasoning) document.getElementById('sug-reason').textContent = s.reasoning;

  const box = document.getElementById('sug-plan');
  box.innerHTML = '';
  box.appendChild(el('p', 'quiet', 'Looking at your chapter…'));

  try {
    C.plan = await api('/api/console/plan', {
      number: s.number, path: s.path, suggestion: s.suggestion,
    });
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(el('p', 'notice bad', e.message));
    return;
  }
  renderPlan();
}

function renderPlan() {
  const box = document.getElementById('sug-plan');
  const p = C.plan;
  box.innerHTML = '';

  if (p.can_apply) {
    const card = el('div', 'card after');
    card.appendChild(el('p', 'card-label',
      'The tool can make this change for you — line ' + p.line_no));
    const before = el('p', 'dline');
    before.appendChild(el('span', 'lbl', 'Now: '));
    before.appendChild(document.createTextNode(p.before));
    const after = el('p', 'dline');
    after.appendChild(el('span', 'lbl', 'After: '));
    after.appendChild(document.createTextNode(p.after));
    card.appendChild(before);
    card.appendChild(after);
    card.appendChild(el('p', 'quiet',
      'Only this one line changes. Every other line of the chapter is left exactly as it is.'));

    const lab = el('label', 'check confirm');
    const cb = el('input');
    cb.type = 'checkbox'; cb.id = 'sug-apply'; cb.checked = true;
    lab.appendChild(cb);
    lab.appendChild(el('span', '', 'Make this change to my chapter as well as replying.'));
    card.appendChild(lab);
    box.appendChild(card);
    return;
  }

  const card = el('div', 'card');
  card.appendChild(el('p', 'card-label', 'This one is for you to do'));
  card.appendChild(el('p', '', p.reason));
  if (p.needs_vault) {
    card.appendChild(el('p', 'quiet',
      'Choose your chapters folder at the bottom of the previous screen and the tool can check the wording for you.'));
  } else {
    card.appendChild(el('p', 'quiet',
      'Accepting sends a thank-you and clears it from this list. Make the change yourself under Chapters.'));
  }
  box.appendChild(card);
}

document.getElementById('sug-back').onclick = () => show('step-console');
document.getElementById('sug-browser').onclick = () => {
  if (C.suggestion) window.open(C.suggestion.url, '_blank', 'noopener');
};

document.getElementById('sug-accept').onclick = async () => {
  const s = C.suggestion;
  if (!s) return;
  const cb = document.getElementById('sug-apply');
  const applyIt = !!(cb && cb.checked && C.plan && C.plan.can_apply);
  const btn = document.getElementById('sug-accept');
  btn.disabled = true;
  try {
    const r = await api('/api/console/accept', { number: s.number, apply: applyIt });
    consoleDone('Accepted', r.steps);
  } catch (e) {
    fail(e.message);
  } finally { btn.disabled = false; }
};

document.getElementById('sug-decline').onclick = async () => {
  const s = C.suggestion;
  if (!s) return;
  const btn = document.getElementById('sug-decline');
  btn.disabled = true;
  try {
    const r = await api('/api/console/decline', { number: s.number });
    consoleDone('Declined, politely', r.steps);
  } catch (e) {
    fail(e.message);
  } finally { btn.disabled = false; }
};

/* --- one draft change --- */

async function openDraft(x) {
  C.draft = x;
  show('step-draft');
  document.getElementById('draft-meta').textContent =
    'Written by ' + x.who + ' on ' + when(x.when);
  const body = document.getElementById('draft-body');
  body.innerHTML = '';
  body.appendChild(el('p', 'quiet', 'Reading the change…'));

  let detail;
  try {
    detail = await api('/api/console/draft-detail', { number: x.number });
  } catch (e) {
    body.innerHTML = '';
    body.appendChild(el('p', 'notice bad', e.message));
    return;
  }
  renderDraft(detail);
}

function renderDraft(d) {
  const body = document.getElementById('draft-body');
  body.innerHTML = '';

  if (!d.readable) {
    const card = el('div', 'card');
    card.appendChild(el('p', 'card-label', 'Too big to show here'));
    card.appendChild(el('p', '', d.why));
    (d.pages || []).forEach(p => {
      card.appendChild(el('p', 'quiet',
        p.page + ' — ' + p.added + ' lines added, ' + p.removed + ' removed'));
    });
    body.appendChild(card);
    return;
  }

  (d.pages || []).forEach(p => {
    const card = el('div', 'card');
    card.appendChild(el('p', 'card-label', p.page));
    if (!p.lines.length) {
      card.appendChild(el('p', 'quiet', 'No wording changed on this page.'));
    }
    p.lines.forEach(l => {
      const row = el('p', 'dline ' + (l.kind === 'after' ? 'is-after' : 'is-before'));
      row.appendChild(el('span', 'lbl', l.kind === 'after' ? 'After: ' : 'Now: '));
      row.appendChild(document.createTextNode(l.text));
      card.appendChild(row);
    });
    body.appendChild(card);
  });
}

document.getElementById('draft-back').onclick = () => show('step-console');
document.getElementById('draft-browser').onclick = () => {
  if (C.draft) window.open(C.draft.url, '_blank', 'noopener');
};

document.getElementById('draft-accept').onclick = async () => {
  const x = C.draft;
  if (!x) return;
  const btn = document.getElementById('draft-accept');
  btn.disabled = true;
  try {
    const r = await api('/api/console/draft-accept', { number: x.number, title: x.title });
    consoleDone('Accepted', r.steps);
  } catch (e) { fail(e.message); } finally { btn.disabled = false; }
};

document.getElementById('draft-decline').onclick = async () => {
  const x = C.draft;
  if (!x) return;
  const btn = document.getElementById('draft-decline');
  btn.disabled = true;
  try {
    const r = await api('/api/console/draft-decline', { number: x.number });
    consoleDone('Declined', r.steps);
  } catch (e) { fail(e.message); } finally { btn.disabled = false; }
};

/* --- afterwards --- */

function consoleDone(title, steps) {
  document.getElementById('cdone-title').textContent = title;
  const list = document.getElementById('cdone-steps');
  list.innerHTML = '';
  (steps || []).forEach(t => list.appendChild(el('li', '', t)));
  show('step-console-done');
}
document.getElementById('cdone-back').onclick = loadConsole;


/* ---------- start ---------- */

document.getElementById('welcome-go').onclick = async () => {
  try { await api('/api/welcome-done', {}); } catch (e) { /* not important */ }
  show('step-choose');
};
document.getElementById('welcome-settings').onclick = async () => {
  try { await api('/api/welcome-done', {}); } catch (e) { /* not important */ }
  await openSettings();
  show('step-choose');
};

(async function boot() {
  startHeartbeat();
  await loadEnv();
  refreshDeepseekOption();
  show(ENV.seen_welcome ? 'step-choose' : 'step-welcome');
})();
