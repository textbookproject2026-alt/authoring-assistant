/* The interface. One question at a time, plain words, nothing written until the
   author has seen the finished file and said yes. */

const TOKEN = document.body.dataset.token;
// Which copy of the app this page is, stamped when it was served. The copy
// running in the background refuses anything from a page of another version,
// and an older copy that predates the stamp leaves it unfilled.
const BUILD = document.body.dataset.build;
const STALE = 'This window and the part of the app running in the background ' +
  'are from different versions, so nothing was done. Please quit and reopen the app.';
let staleSeen = BUILD === '__BUILD__';

const S = {
  sessionId: null,
  mode: null,           // "file", "folder", or "drafts" for the book's drafts area
  head: null,           // for "drafts": the drafts commit the chapter was read at
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
    headers: { 'Content-Type': 'application/json', 'X-AA-Token': TOKEN,
               'X-AA-Build': BUILD || '' },
    body: JSON.stringify(body || {}),
  });
  let data;
  try { data = await res.json(); } catch (e) {
    throw new Error('The tool stopped responding. Please close this tab and start it again.');
  }
  // An older copy that predates the version check answers what it has never
  // heard of this way, and nothing else would.
  if (data.stale || (res.status === 404 && data.error === 'Unknown request.')) {
    staleSeen = true;
    throw new Error(STALE);
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
  renderWorkspace(data.workspace);
  S.sessionId = data.session_id;
  S.mode = data.mode;
  S.head = null;
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

// A chapter as the chosen book's drafts area holds it. No folder is needed,
// and what the author says yes to goes back there as one commit.
document.getElementById('pick-drafts').onclick = async () => {
  if (!bookSlug()) {
    return fail('Choose a book first: press “Change book” at the top of the page.');
  }
  const note = document.getElementById('choose-note');
  note.textContent = 'Reading the drafts area of ' + bookName() + '…';
  try {
    const data = await bookApi('/api/drafts/open', {});
    note.textContent = '';
    renderWorkspace(data.workspace);
    S.sessionId = data.session_id;
    S.mode = 'drafts';
    S.head = data.head;
    S.root = null;
    S.chapters = data.chapters;
    document.getElementById('vault-summary').textContent =
      `${data.chapters.length} chapters in ${data.root_name} (${data.repo}, “${data.branch}”)`;
    renderChapters('');
    show('step-chapter');
  } catch (e) { note.textContent = ''; fail(e.message); }
};

// "Download a copy": the book's files as the drafts area holds them, into a
// new folder. Nothing on this Mac is written over.
document.getElementById('download-copy').onclick = async () => {
  if (!bookSlug()) {
    return fail('Choose a book first: press “Change book” at the top of the page.');
  }
  const note = document.getElementById('download-note');
  note.textContent = 'The chooser window is open — it may be behind this one.';
  note.classList.remove('hidden');
  try {
    const r = await bookApi('/api/drafts/download', {});
    if (r.cancelled) { note.classList.add('hidden'); return; }
    if (r.error) { note.classList.add('hidden'); return fail(r.error); }
    note.textContent = `A copy of ${bookName()} (${r.files} files, from “${r.branch}”) is in ` +
      `${r.folder}.` + (r.left_out ? ` ${r.left_out} links were left out.` : '');
  } catch (e) { note.classList.add('hidden'); fail(e.message); }
};

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
    const info = await bookApi('/api/prepare', { session_id: S.sessionId, chapter: path });
    S.chapter = info;
    if (info.mode === 'drafts') S.head = info.head;
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

  const toDrafts = S.mode === 'drafts';
  document.getElementById('preview-file').textContent = !S.chapter ? ''
    : S.chapter.chapter + (toDrafts ? ' — in the drafts area of ' + bookName() : '');
  document.getElementById('commit-warn-folder').classList.toggle('hidden', toDrafts);
  document.getElementById('commit-warn-drafts').classList.toggle('hidden', !toDrafts);
  document.getElementById('confirm-text').textContent = toDrafts
    ? 'I have read the changes above and I want to send them to the drafts area.'
    : 'I have read the changes above and I want to save them.';
  document.getElementById('do-commit').textContent =
    toDrafts ? 'Send to drafts' : 'Save these changes';

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
  document.getElementById('working-note').textContent =
    S.mode === 'drafts' ? 'Sending to the drafts area…' : 'Saving…';
  try {
    const r = await bookApi('/api/commit', {
      session_id: S.sessionId,
      head: S.head,
      accepted,
      expand_groups: S.expandGroups,
    });
    if (r.moved) {
      // Nothing was sent. The drafts area has been read again: if this
      // chapter is as it was, the same choices are offered on top of what is
      // there now; if not, the chapter is gone through again.
      S.head = r.head;
      fail(r.message);
      if (r.same) {
        show('step-preview');
        document.getElementById('confirm-box').checked = false;
        return;
      }
      return chooseChapter(S.chapter.chapter);
    }
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
  document.querySelector('#step-done h2').textContent = r.sent ? 'Sent to drafts' : 'Saved';

  box.appendChild(list);
  if (r.sent) {
    S.head = r.head;
    box.appendChild(el('p', 'quiet', `Sent to the drafts area of ${bookName()} ` +
      `(${r.repo}, “${r.branch}”) as one change: ` + r.written.join('  •  ')));
    box.appendChild(el('p', '', 'Readers don’t see the drafts area. It reaches them the next time the book goes live.'));
  } else if (r.written.length) {
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

/* ---------- bringing in a Word document ---------- */
/* Nothing reaches the vault until the author has read the converted chapter and
   ticked the box. Everything before that happens in a temporary folder. */

const W = {
  docx: null,
  folder: null,
  converted: null,  // what the server said came out
  saved: null,      // { chapter, media } once written into the vault
  drafts: null,     // what sending to the drafts area would do, as last read
  sent: null,       // { sha, url, ... } once sent to the drafts area
};

document.getElementById('pick-docx').onclick = enterImport;

async function enterImport() {
  let st;
  try { st = await api('/api/import/status', {}); } catch (e) { return fail(e.message); }
  if (!st.ready) {
    document.getElementById('install-message').textContent = '';
    return show('step-import-setup');
  }
  // Until the author picks another, a chapter goes in the book's chapters
  // folder, not wherever the chooser happens to open.
  if (st.folder && (!W.folder || W.folder.folder !== st.folder)) {
    W.folder = { folder: st.folder, folder_name: st.folder_name,
                 chapters_here: st.chapters_here || 0 };
  } else if (!st.folder) {
    W.folder = null;
  }
  renderImportStep();
  show('step-import');
}

function describeFolder(f) {
  return f.folder + (f.chapters_here
    ? `  —  ${f.chapters_here} chapter${f.chapters_here === 1 ? '' : 's'} already here`
    : '  —  no chapters here yet') +
    (f.at_top ? '. This is the top of your vault; chapters usually go in its “chapters” folder.' : '');
}

document.getElementById('import-setup-back').onclick = () => show('step-choose');

document.getElementById('install-recheck').onclick = async () => {
  const msg = document.getElementById('install-message');
  msg.textContent = 'Looking…';
  msg.className = 'quiet';
  try {
    const st = await api('/api/import/status', {});
    if (st.ready) {
      ENV.pandoc = true;
      renderImportStep();
      return show('step-import');
    }
    msg.textContent = 'It is still not there. If the installer window is open, ' +
      'finish it first, then press this again.';
    msg.className = 'quiet bad';
  } catch (e) { msg.textContent = e.message; msg.className = 'quiet bad'; }
};

document.getElementById('install-pandoc').onclick = async () => {
  const btn = document.getElementById('install-pandoc');
  const msg = document.getElementById('install-message');
  btn.disabled = true;
  msg.className = 'quiet';
  msg.textContent = 'Downloading. This takes a minute or two — please leave this ' +
    'page open.';
  try {
    const r = await api('/api/import/install', {});
    msg.textContent = r.message;
    msg.className = 'quiet good';
  } catch (e) {
    msg.textContent = e.message;
    msg.className = 'quiet bad';
  } finally { btn.disabled = false; }
};

function renderImportStep() {
  document.getElementById('docx-chosen').textContent =
    W.docx ? W.docx.docx_name : 'Nothing chosen yet.';
  document.getElementById('folder-chosen').textContent =
    W.folder ? describeFolder(W.folder) : 'Nothing chosen yet.';
  checkImportReady();
  refreshDraftsStatus();
}

/* Whether the chapter can go to the book's drafts area is said here, before
   anything is converted, so an author whose account can't change the book is
   told now and not after reading the whole chapter. */
async function refreshDraftsStatus() {
  const p = document.getElementById('import-drafts-status');
  try {
    const r = await api('/api/import/drafts-status', {});
    p.textContent = r.message;
    p.className = r.available ? '' : 'quiet';
    showAccountFix('import-account-actions', 'import-switch-account', r.why);
  } catch (e) { p.textContent = e.message; p.className = 'quiet bad'; }
}

/* When it is the account that stops a chapter going to drafts, the way to sign
   in as another one is offered right there. */
function showAccountFix(boxId, buttonId, why) {
  const fix = why === 'no_access' || why === 'signed_out';
  document.getElementById(boxId).classList.toggle('hidden', !fix);
  document.getElementById(buttonId).textContent =
    why === 'signed_out' ? 'Sign in' : 'Use a different account';
}

async function importAccountFix(from, why) {
  if (why === 'signed_out') {
    C.returnTo = from;
    C.leaving = null;
    return enterConsole();
  }
  await switchAccount(from);
}
document.getElementById('import-switch-account').onclick = async () => {
  const r = await api('/api/import/drafts-status', {}).catch(() => ({}));
  await importAccountFix('import', r.why);
};
document.getElementById('import-preview-switch-account').onclick = () =>
  importAccountFix('import-preview', W.converted && W.converted.drafts && W.converted.drafts.why);

function checkImportReady() {
  const name = document.getElementById('import-name').value.trim();
  const problem = document.getElementById('name-problem');
  let why = '';
  if (name && /[/\\:*?"<>|]/.test(name)) {
    why = 'A chapter name cannot contain any of these: / \\ : * ? " < > |';
  }
  problem.textContent = why;
  document.getElementById('do-convert').disabled =
    !(W.docx && W.folder && name && !why);
}
document.getElementById('import-name').oninput = checkImportReady;

// The suggested name follows the Word file and the folder (chapter-NN in a
// book's chapters folder). It replaces the box only while the author hasn't
// typed a name of their own.
const NAME_HOW = {
  recorded: 'This Word file became this chapter last time, so bringing it in again replaces it.',
  existing: 'A chapter already has this Word file\'s name, so bringing it in again replaces it.',
  new: 'The next free chapter number.',
};
function applySuggestion(r) {
  if (!r || !r.suggested_name) return;
  const nameBox = document.getElementById('import-name');
  const typed = nameBox.value.trim();
  if (!typed || typed === W.suggested) {
    nameBox.value = r.suggested_name;
    document.getElementById('name-how').textContent = NAME_HOW[r.suggested_how] || '';
  }
  W.suggested = r.suggested_name;
}

document.getElementById('choose-docx').onclick = async () => {
  try {
    const r = await api('/api/import/pick-docx', {});
    if (r.cancelled) return;
    if (r.error) return fail(r.error);
    W.docx = r;
    applySuggestion(r);
    renderImportStep();
  } catch (e) { fail(e.message); }
};

document.getElementById('choose-import-folder').onclick = async () => {
  try {
    const r = await api('/api/import/pick-folder', {});
    if (r.cancelled) return;
    if (r.error) return fail(r.error);
    await refreshWorkspace();
    W.folder = r;
    applySuggestion(r);
    refreshDraftsStatus();
    document.getElementById('folder-chosen').textContent = describeFolder(r);
    checkImportReady();
  } catch (e) { fail(e.message); }
};

document.getElementById('do-convert').onclick = async () => {
  show('step-working');
  document.getElementById('working-note').textContent =
    'Reading the Word document. A long chapter with many pictures can take a moment.';
  try {
    const r = await api('/api/import/convert', {
      name: document.getElementById('import-name').value.trim(),
    });
    W.saved = null; W.sent = null; W.drafts = null;
    renderImportPreview(r);
    show('step-import-preview');
  } catch (e) { fail(e.message); show('step-import'); }
};

function renderImportPreview(r) {
  const c = r.counts;
  const bits = [`${c.words.toLocaleString()} words`];
  if (c.headings) bits.push(`${c.headings} heading${c.headings === 1 ? '' : 's'}`);
  if (c.pipe_tables + c.html_tables)
    bits.push(`${c.pipe_tables + c.html_tables} table${c.pipe_tables + c.html_tables === 1 ? '' : 's'}`);
  if (c.footnotes) bits.push(`${c.footnotes} footnote${c.footnotes === 1 ? '' : 's'}`);
  if (c.pictures) bits.push(`${c.pictures} picture${c.pictures === 1 ? '' : 's'}`);
  document.getElementById('import-summary').textContent =
    `${W.docx.docx_name} became a chapter of ${bits.join(', ')}.`;

  const box = document.getElementById('import-notes');
  box.innerHTML = '';
  r.notes.forEach(n => {
    const d = el('div', 'finding-note ' + n.level);
    d.appendChild(el('div', 'fn-head', n.headline));
    d.appendChild(el('div', 'fn-body', n.body));
    if (n.check) {
      const c2 = el('div', 'fn-check');
      c2.appendChild(el('strong', '', 'What to check: '));
      c2.appendChild(document.createTextNode(n.check));
      d.appendChild(c2);
    }
    box.appendChild(d);
  });

  document.getElementById('import-target').textContent =
    'It will be saved as ' + r.path;
  document.getElementById('import-text').textContent = r.text;

  const mBlock = document.getElementById('import-media-block');
  const mList = document.getElementById('import-media-list');
  mList.innerHTML = '';
  if (r.media.length) {
    mBlock.classList.remove('hidden');
    document.getElementById('import-media-note').textContent =
      `These will be saved in your textbook's pictures folder, in a folder of their own — “${r.media_rel}”.`;
    r.media.forEach(m => {
      const li = el('li');
      li.appendChild(el('strong', '', m.name));
      li.appendChild(document.createTextNode(
        `  —  ${m.ext ? m.ext.toUpperCase() : 'unknown kind'}, ${Math.max(1, Math.round(m.size / 1024))} KB`));
      mList.appendChild(li);
    });
  } else {
    mBlock.classList.add('hidden');
  }

  W.converted = r;
  document.getElementById('import-local-note').textContent = r.local_problem
    ? 'It can\'t be saved into your vault: ' + r.local_problem : '';
  renderDraftsIntro(r.drafts);

  document.getElementById('import-confirm').checked = false;
  document.getElementById('do-import-save').disabled = true;
  updateImportButtons();
}

function renderDraftsIntro(d) {
  const block = document.getElementById('import-drafts-block');
  block.classList.remove('hidden');
  document.getElementById('import-drafts-where').textContent = d ? d.message : '';
  showAccountFix('import-preview-account-actions', 'import-preview-switch-account',
                 d && d.why);
  document.getElementById('import-drafts-detail').textContent = '';
  document.getElementById('import-drafts-removed').innerHTML = '';
  document.getElementById('import-drafts-problem').textContent = '';
  document.getElementById('import-replace-label').classList.add('hidden');
  document.getElementById('import-replace').checked = false;
  if (d && d.available) checkDrafts();
}

async function checkDrafts() {
  document.getElementById('import-drafts-detail').textContent =
    'Looking at the drafts area…';
  try {
    W.drafts = await api('/api/import/drafts-check', { book: bookSlug() });
    renderDrafts(W.drafts);
  } catch (e) {
    W.drafts = null;
    document.getElementById('import-drafts-detail').textContent = '';
    document.getElementById('import-drafts-problem').textContent = e.message;
  }
  updateImportButtons();
}

function renderDrafts(d) {
  const pics = W.converted && W.converted.media.length
    ? ` Its pictures go in “${d.media_dir}”.` : '';
  document.getElementById('import-drafts-where').textContent =
    `It will go to the drafts area of ${d.repo} (“${d.branch}”) as “${d.chapter_path}”, ` +
    `as one change made by you.` + pics;
  let detail = '';
  if (d.exists) {
    const last = d.last
      ? ` It was last changed by ${d.last.who || 'someone'}` +
        (d.last.when ? ` on ${new Date(d.last.when).toLocaleString()}` : '') +
        (d.last.message ? ` (“${d.last.message}”)` : '') + '.'
      : '';
    const lines = d.changed_lines
      ? ` Sending replaces it with this one: ${d.changed_lines.removed} line` +
        `${d.changed_lines.removed === 1 ? '' : 's'} taken out, ${d.changed_lines.added} put in.`
      : ' Sending replaces it with this one.';
    detail = 'A chapter of this name is already in the drafts area.' + last + lines +
      ' Anything in it that isn\'t in your Word document — an edit published from the ' +
      'browser editor, say — is replaced too. It stays in the drafts area\'s history.';
  }
  if (d.nothing_to_send) detail = 'The drafts area already has exactly this chapter ' +
    'and these pictures, so there is nothing to send.';
  document.getElementById('import-drafts-detail').textContent = detail;

  const removed = document.getElementById('import-drafts-removed');
  removed.innerHTML = '';
  if (d.removed.length) {
    removed.appendChild(el('li', '', 'These pictures are in the drafts area from an ' +
      'earlier import and aren\'t in this one, so they will be taken out: ' +
      d.removed.join(', ') + '.'));
  }
  if (d.contents && d.contents.line && !d.refused && !d.nothing_to_send) {
    removed.appendChild(el('li', '', 'So readers can find it, the front page gets one new ' +
      'line under “Contents”, in the same change: ' + d.contents.line +
      ' Nothing else on the front page changes.'));
  } else if (d.contents && d.contents.note && !d.refused && !d.nothing_to_send) {
    removed.appendChild(el('li', '', d.contents.note));
  }
  document.getElementById('import-drafts-problem').textContent = d.refused || '';
  document.getElementById('import-replace-label').classList.toggle(
    'hidden', !(d.exists && !d.refused && !d.nothing_to_send));
  document.getElementById('import-replace').checked = false;
}

function updateImportButtons(confirmed) {
  const ok = confirmed === undefined
    ? document.getElementById('import-confirm').checked : confirmed;
  const r = W.converted || {};
  document.getElementById('do-import-save').disabled =
    !ok || !!r.local_problem || !!W.saved;
  const d = W.drafts;
  document.getElementById('do-send-drafts').disabled = !ok || !!W.sent || !d ||
    !!d.refused || d.nothing_to_send ||
    (d.exists && !document.getElementById('import-replace').checked);
}

document.getElementById('import-confirm').onchange = e => {
  document.getElementById('do-import-save').disabled = !e.target.checked;
  updateImportButtons(e.target.checked);
};
document.getElementById('import-replace').onchange = () => updateImportButtons();

document.getElementById('do-send-drafts').onclick = async () => {
  document.getElementById('do-send-drafts').disabled = true;
  show('step-working');
  document.getElementById('working-note').textContent = 'Sending to the drafts area…';
  try {
    const r = await api('/api/import/drafts-send', {
      book: bookSlug(), head: W.drafts.head,
      replace: document.getElementById('import-replace').checked,
    });
    if (r.moved) {
      /* Nothing was sent. What is there now is shown, and the author has to
         look and say yes again. */
      W.drafts = r.drafts;
      renderDrafts(r.drafts);
      document.getElementById('import-drafts-problem').textContent =
        r.message + (r.drafts.refused ? ' ' + r.drafts.refused : '');
      document.getElementById('import-confirm').checked = false;
      updateImportButtons();
      return show('step-import-preview');
    }
    W.sent = r;
    renderImportDone();
    show('step-import-done');
  } catch (e) {
    fail(e.message);
    show('step-import-preview');
    document.getElementById('import-confirm').checked = false;
    updateImportButtons();
  }
};

document.getElementById('import-preview-back').onclick = async () => {
  try { await api('/api/import/cancel', {}); } catch (e) { /* nothing was written */ }
  show('step-import');
};

document.getElementById('do-import-save').onclick = async () => {
  document.getElementById('do-import-save').disabled = true;
  show('step-working');
  document.getElementById('working-note').textContent = 'Saving…';
  try {
    const r = await api('/api/import/save', {});
    W.saved = r;
    renderImportDone();
    show('step-import-done');
  } catch (e) {
    fail(e.message);
    show('step-import-preview');
    document.getElementById('import-confirm').checked = false;
    updateImportButtons();
  }
};

function renderImportDone() {
  const r = W.saved, s = W.sent;
  document.getElementById('import-done-title').textContent =
    r && s ? 'The chapter is in your vault and in the drafts area'
      : s ? 'The chapter is in the drafts area' : 'The chapter is in your vault';
  const box = document.getElementById('import-done-summary');
  box.innerHTML = '';
  const list = el('ul', 'summary-list');
  if (r) {
    list.appendChild(el('li', '', `Your new chapter is ${r.chapter}`));
    if (r.media) list.appendChild(el('li', '',
      `Its pictures are in ${r.media_name || r.media}, inside your textbook`));
  }
  if (s) {
    list.appendChild(el('li', '',
      `It is in the drafts area of ${s.repo} (“${s.branch}”) as ${s.chapter_path}, ` +
      `in one change made by you` +
      (s.removed ? `, which also took out ${s.removed} picture${s.removed === 1 ? '' : 's'} ` +
        'the Word document no longer has' : '') + '.'));
    if (s.contents) list.appendChild(el('li', '',
      'The front page lists it under “Contents”, in the same change.'));
    list.appendChild(el('li', '', 'Readers don\'t see it until the drafts go live.'));
  }
  list.appendChild(el('li', '',
    'Your Word document has not been changed or moved. It is still where it was.'));
  box.appendChild(list);
  if (s && s.url) {
    const a = el('a', '', 'See the change');
    a.href = s.url; a.target = '_blank'; a.rel = 'noopener';
    box.appendChild(a);
  }
  if (r) box.appendChild(el('p', '',
    'If Obsidian is open, the new chapter appears in it on its own.'));
  document.getElementById('import-preview-status').textContent = '';
  if (s) checkDraftsPreview('import');
  else document.getElementById('import-preview').classList.add('hidden');

  document.getElementById('import-analyse').classList.toggle('hidden', !r);
  document.getElementById('import-analyse-card').classList.toggle('hidden', !r);
  const other = document.getElementById('import-other-way');
  const c = W.converted || {};
  const canSend = c.drafts && c.drafts.available && !s;
  const canSave = !c.local_problem && !r;
  other.textContent = canSend ? 'Send it to drafts as well'
    : canSave ? 'Save it into your vault as well' : '';
  other.classList.toggle('hidden', !(canSend || canSave));
}

/* Back to the same converted chapter, to do the other of the two. It has to be
   looked at and ticked again, and the drafts area is read again. */
document.getElementById('import-other-way').onclick = () => {
  document.getElementById('import-confirm').checked = false;
  if (W.converted && W.converted.drafts && W.converted.drafts.available && !W.sent) {
    checkDrafts();
  }
  updateImportButtons();
  show('step-import-preview');
};

document.getElementById('import-another').onclick = () => {
  W.docx = null;
  W.saved = null; W.sent = null; W.drafts = null; W.converted = null;                  // the folder is kept: it is usually the same one
  document.getElementById('import-name').value = '';
  document.getElementById('name-how').textContent = '';
  W.suggested = null;
  renderImportStep();
  show('step-import');
};

document.getElementById('import-finish').onclick = () => show('step-choose');

/* Straight from the new chapter into the three analyses, so it arrives linked
   rather than raw. This is the same path as choosing it from the front screen. */
document.getElementById('import-analyse').onclick = async () => {
  if (!W.saved) return show('step-choose');
  show('step-working');
  document.getElementById('working-note').textContent = 'Opening your new chapter…';
  try {
    await openTarget(W.saved.chapter);
  } catch (e) {
    fail(e.message);
    show('step-import-done');
  }
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
    ['Reading Word documents', ENV.pandoc
      ? (ENV.pandoc_where === 'bundled'
          ? `ready — a copy of pandoc ${ENV.pandoc_version || ''} comes with this app`.trim()
          : `ready — using the pandoc ${ENV.pandoc_version || ''} already on this Mac`.trim())
      : 'not set up — the rest of the tool works as normal; you are offered the '
        + 'one-off install when you first bring in a Word document'],
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
  renderSettingsAccount();
  document.getElementById('settings').classList.remove('hidden');
}

function renderSettingsAccount() {
  const st = C.status || {};
  const p = document.getElementById('settings-account');
  p.textContent = st.signed_in
    ? ('Signed in to GitHub as ' + (st.login || 'an account not yet checked') + '.')
    : 'Not signed in.';
  document.getElementById('settings-account-actions').classList.toggle('hidden', !st.signed_in);
}

document.getElementById('settings-switch-account').onclick = () => switchAccount();
document.getElementById('settings-signout').onclick = async () => {
  if (!(await signOut(false))) return;
  try { C.status = await api('/api/console/status', {}); } catch (e) { /* shown as absent */ }
  renderSettingsAccount();
  if (onConsoleScreen()) await enterConsole();
};

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
  stopHeartbeat();
  try { await api('/api/quit', {}); } catch (e) { /* already going */ }
  document.getElementById('stopped-away').classList.add('hidden');
  show('step-stopped');
};

// The page checks in every 5 seconds. A browser runs a background tab's timers
// late, a minute apart or more, so each check-in says whether the tab is
// hidden and the tool waits longer for a hidden one. A check-in that fails
// while the tab is showing means the tool has stopped all the same (the Mac
// slept, or the browser froze the tab), and the author is told so rather than
// left with a page that no longer answers.
let heartbeat = null;
async function beat() {
  try {
    await fetch('/api/ping?t=' + encodeURIComponent(TOKEN), {
      method: 'POST', headers: { 'X-AA-Token': TOKEN },
      body: JSON.stringify({ hidden: !!document.hidden }),
    });
  } catch (e) {
    if (heartbeat && !document.hidden) {
      stopHeartbeat();
      document.getElementById('stopped-away').classList.remove('hidden');
      show('step-stopped');
    }
  }
}
function startHeartbeat() {
  if (heartbeat) return;
  beat();
  heartbeat = setInterval(beat, 5000);
}
function stopHeartbeat() {
  if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
}
document.addEventListener('visibilitychange', () => { if (heartbeat) beat(); });

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

const C = {
  status: null,
  leaving: null,    // the account just signed out of to use another, or null
  returnTo: null,   // where to go back to once signed in again
  data: null,
  suggestion: null,
  plan: null,
  draft: null,
  publish: null,
  poll: null,
};

/* ---------- which book ---------- */
/* Every queue, count and action in the console belongs to one book, and the
   bar under the header always says which. The book the author chooses decides
   it, not an open vault: a vault is only this Mac's copy of the chosen book,
   and choosing another book closes it. Nothing about a book is written here:
   its repository, branches and links all come from the app, which takes them
   from the list of textbooks. */

let WS = { book: null, vault: null, can_write_vault: false, registry: {} };

function bookSlug() { return WS.book ? WS.book.slug : null; }
function bookName() { return WS.book ? WS.book.title : ''; }

// Every console request says which book the screen was drawn for, and the app
// refuses it if that is no longer the book it is working on.
function bookApi(route, body) {
  return api(route, Object.assign({ book: bookSlug() }, body || {}));
}

function renderWorkspace(ws) {
  if (!ws) return;
  const before = bookSlug();
  WS = ws;

  const bookEl = document.getElementById('bookbar-book');
  bookEl.innerHTML = '';
  if (ws.book) {
    bookEl.appendChild(el('strong', '', ws.book.title));
    bookEl.appendChild(el('span', 'bookbar-repo', ' · ' + ws.book.repo));
    if (ws.book.access === 'read' || ws.book.access === 'none') {
      bookEl.appendChild(el('span', 'bookbar-repo', ' · you can read this book but not change it'));
      // The moment it matters is the moment to offer the way out.
      const other = el('button', 'link inline', 'Use a different account');
      other.onclick = () => switchAccount();
      bookEl.appendChild(other);
    }
  } else {
    bookEl.textContent = 'No book chosen';
  }

  const vaultEl = document.getElementById('bookbar-vault');
  const v = ws.vault;
  vaultEl.textContent = v
    ? v.name
    : 'None open — not needed to work in the drafts area';

  const problem = document.getElementById('bookbar-problem');
  const vaultProblem = v && v.state !== 'ok' ? v.message : '';
  problem.textContent = vaultProblem;
  problem.classList.toggle('hidden', !vaultProblem);

  const reg = ws.registry || {};
  const regEl = document.getElementById('bookbar-registry');
  const regText = reg.source === null || reg.source === undefined
    ? (reg.problem || '')
    : (!reg.fresh ? 'List of textbooks as of ' + (reg.as_of || 'an earlier date') + '.' : '');
  regEl.textContent = regText;
  regEl.classList.toggle('hidden', !regText);

  // Changing book is always offered, vault or no vault.
  document.getElementById('bookbar-change').textContent = 'Change book';
  document.getElementById('bookbar-close').classList.toggle('hidden', !v);

  renderAccount(ws.account);

  // What was on screen belonged to the old book; never leave it showing.
  if (before !== bookSlug()) {
    C.data = null; C.suggestion = null; C.plan = null; C.draft = null; C.publish = null;
    setWaitingCount(0);
  }
  ['console-book', 'sug-book', 'draft-book', 'publish-book'].forEach(id => {
    document.getElementById(id).textContent = ws.book ? ('For ' + ws.book.title) : '';
  });
}

/* Which GitHub account everything is done as. Shown in the bar at all times,
   with the way to sign out or change it beside it. */
function renderAccount(a) {
  if (!a) return;
  const acc = document.getElementById('bookbar-account');
  acc.innerHTML = '';
  if (a.signed_in && a.login) {
    acc.appendChild(el('strong', '', a.login));
    if (a.name && a.name !== a.login) acc.appendChild(el('span', 'bookbar-repo', ' · ' + a.name));
  } else if (a.signed_in) {
    acc.textContent = 'Signed in (checking which account…)';
  } else {
    acc.textContent = 'Not signed in';
  }
  document.getElementById('bookbar-signin').classList.toggle('hidden', !!a.signed_in);
  document.getElementById('bookbar-switch-account').classList.toggle('hidden', !a.signed_in);
  document.getElementById('bookbar-signout').classList.toggle('hidden', !a.signed_in);
}

// Asks who the sign-in belongs to, which needs the network, so the bar can
// name the account rather than just say someone is signed in.
async function refreshAccount() {
  try { renderWorkspace(await api('/api/account', {})); } catch (e) { /* the bar keeps its last state */ }
}

async function refreshWorkspace() {
  try { renderWorkspace(await api('/api/workspace', {})); } catch (e) { /* the bar keeps its last state */ }
}

function onConsoleScreen() {
  return ['step-console', 'step-suggestion', 'step-draft', 'step-publish',
          'step-console-done', 'step-books']
    .some(id => !document.getElementById(id).classList.contains('hidden'));
}

async function openBookPicker() {
  place('console');
  stopPolling();
  show('step-books');
  const list = document.getElementById('books-list');
  const note = document.getElementById('books-note');
  const hidden = document.getElementById('books-hidden');
  const vaultCard = document.getElementById('books-vault');
  list.innerHTML = '';
  hidden.textContent = '';
  note.classList.add('hidden');

  // The book chosen here decides. A vault open now is a copy of one book, so
  // choosing a different one closes it; say so before, not after.
  vaultCard.classList.toggle('hidden', !(WS.vault && WS.book && WS.can_write_vault));
  if (WS.vault && WS.book && WS.can_write_vault) {
    document.getElementById('books-vault-text').textContent =
      '“' + WS.vault.name + '” is your copy of ' + WS.book.title +
      '. Choosing a different book closes it.';
  }

  list.appendChild(el('li', 'none', 'Checking which books you can work on…'));
  let r;
  try { r = await api('/api/books', {}); } catch (e) {
    list.innerHTML = '';
    return fail(e.message);
  }
  renderWorkspace(r.workspace);
  list.innerHTML = '';
  if (r.note) {
    note.textContent = r.note;
    note.classList.remove('hidden');
  }
  const invites = document.getElementById('books-invites');
  invites.innerHTML = '';
  const inv = r.invitations || [];
  document.getElementById('books-invites-box').classList.toggle('hidden', !inv.length);
  inv.forEach(b => {
    const li = el('li');
    const btn = el('button');
    btn.appendChild(el('strong', '', b.title));
    btn.appendChild(el('span', 'who', 'Accept the invitation and start working on it' +
      (b.inviter ? ' · from ' + b.inviter : '')));
    btn.onclick = () => acceptInvitation(b.invitation, btn);
    li.appendChild(btn);
    invites.appendChild(li);
  });
  const inviteNote = document.getElementById('books-invite-note');
  inviteNote.textContent = r.invite_note || '';
  inviteNote.classList.toggle('hidden', !r.invite_note);
  r.books.forEach(b => {
    const li = el('li');
    const btn = el('button');
    btn.appendChild(el('strong', '', b.title));
    btn.appendChild(el('span', 'who', b.repo + (b.status === 'preview' ? ' · not yet public' : '')));
    if (WS.book && WS.book.slug === b.slug) btn.appendChild(el('span', 'snip', 'The book you are working on now.'));
    btn.onclick = () => chooseBook(b.slug);
    li.appendChild(btn);
    list.appendChild(li);
  });
  if (!r.books.length && !inv.length) {
    list.appendChild(el('li', 'none',
      'Your account can’t make changes to any registered book. The book’s maintainer can give you access.'));
  }
  document.getElementById('books-other-account').classList.toggle(
    'hidden', !(r.hidden || !r.books.length));
  if (r.hidden) {
    hidden.textContent = r.hidden === 1
      ? '1 other book isn’t shown, because your account can’t make changes to it.'
      : r.hidden + ' other books aren’t shown, because your account can’t make changes to them.';
  }
}

async function acceptInvitation(id, btn) {
  if (btn) btn.disabled = true;
  let r;
  try { r = await api('/api/books/accept', { invitation: id }); } catch (e) {
    if (btn) btn.disabled = false;
    return fail(e.message);
  }
  // Accepted: the book is theirs to work on, so it is chosen straight away.
  if (r.access === 'write') return chooseBook(r.slug);
  return openBookPicker();
}

async function chooseBook(slug) {
  const hadVault = !!WS.vault;
  try {
    renderWorkspace(await api('/api/books/choose', { slug }));
  } catch (e) { return fail(e.message); }
  // A Word import on its way into a vault that closed went with it.
  if (hadVault && !WS.vault) {
    W.folder = null; W.converted = null; W.saved = null; W.sent = null; W.drafts = null;
  }
  await loadConsole();
}

document.getElementById('bookbar-change').onclick = switchBook;
document.getElementById('books-back').onclick = () => document.getElementById('go-chapters').click();

async function closeVault() {
  try { renderWorkspace(await api('/api/vault/close', {})); } catch (e) { return fail(e.message); }
  if (onConsoleScreen()) await enterConsole();
}
document.getElementById('bookbar-close').onclick = closeVault;

// Work on a different book: straight to the list. Nothing closes until
// another book is actually chosen.
async function switchBook() {
  await enterBookPicker();
}
document.getElementById('books-switch-account').onclick = () => switchAccount('books');

// Choosing a book needs to know who is asking, so it goes through sign-in.
async function enterBookPicker() {
  await enterConsole({ picker: true });
}


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

async function enterConsole(opts) {
  place('console');
  stopPolling();
  try {
    C.status = await api('/api/console/status', {});
  } catch (e) { return fail(e.message); }
  renderWorkspace(C.status.workspace);

  if (!C.status.configured) return show('step-console-setup');
  if (!C.status.signed_in) {
    document.getElementById('signin-code-box').classList.add('hidden');
    renderSwitching();
    return show('step-console-signin');
  }
  // No book yet, or the author asked to change it: choose one first. The
  // console never shows a queue without knowing whose it is.
  if (!WS.book || (opts && opts.picker)) return openBookPicker();
  await loadConsole();
}

document.getElementById('go-console').onclick = () => enterConsole();
document.getElementById('go-chapters').onclick = () => {
  place('chapters');
  stopPolling();
  show(S.sessionId ? 'step-choose' : 'step-choose');
};
document.getElementById('setup-back').onclick = () => document.getElementById('go-chapters').click();
document.getElementById('signin-back').onclick = () => {
  stopPolling();
  document.getElementById('signin-start').disabled = false;
  const back = C.returnTo;
  C.returnTo = null; C.leaving = null;
  if (back === 'import') { place('chapters'); renderImportStep(); return show('step-import'); }
  if (back === 'import-preview' && W.converted) { place('chapters'); return show('step-import-preview'); }
  document.getElementById('go-chapters').click();
};
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
      const leaving = C.leaving;
      C.leaving = null;
      renderSwitching();
      await refreshAccount();
      if (r.same_account) {
        fail('GitHub signed you in as ' + r.login + ' again, the account you ' +
          'signed out of. It uses whichever account your web browser is signed ' +
          'in to. To use another one, sign out of GitHub in your browser, then ' +
          'press “Use a different account” again.');
      }
      await afterSignin(leaving);
    } catch (e) {
      document.getElementById('signin-start').disabled = false;
      document.getElementById('signin-code-box').classList.add('hidden');
      fail(e.message);
    }
  }, Math.max(2, wait) * 1000);
}

/* Signing out takes the sign-in out of the Keychain; the server reads it back
   to make sure. If it could not, the author is told they are still signed in,
   never shown a signed-out screen over a token that is still there. */
async function signOut(switching) {
  let r;
  try {
    r = await api('/api/console/signout', { switching: !!switching });
  } catch (e) {
    await refreshWorkspace();
    fail(e.message);
    return false;
  }
  C.status = null; C.data = null;
  setWaitingCount(0);
  renderWorkspace(r.workspace);
  C.leaving = switching ? (r.was || '') : null;
  return true;
}

// `from` says where to come back to once signed in as the other account.
async function switchAccount(from) {
  document.getElementById('settings').classList.add('hidden');
  if (!(await signOut(true))) return;
  C.returnTo = from || null;
  await enterConsole();
}

function renderSwitching() {
  const box = document.getElementById('signin-switching');
  box.classList.toggle('hidden', C.leaving === null || C.leaving === undefined);
  document.getElementById('signin-switching-text').textContent = C.leaving
    ? ('You have signed out of ' + C.leaving + '. Its sign-in has been taken out of this Mac\'s Keychain.')
    : 'The old sign-in has been taken out of this Mac\'s Keychain.';
}

async function afterSignin(leaving) {
  const back = C.returnTo;
  C.returnTo = null;
  if (back === 'import') {
    place('chapters');
    renderImportStep();
    return show('step-import');
  }
  if (back === 'import-preview' && W.converted) {
    place('chapters');
    try {
      const d = await api('/api/import/drafts-status', {});
      W.converted.drafts = d;
    } catch (e) { /* said on the screen below */ }
    renderDraftsIntro(W.converted.drafts);
    document.getElementById('import-confirm').checked = false;
    updateImportButtons();
    return show('step-import-preview');
  }
  if (back === 'books') return enterBookPicker();
  await enterConsole();
}

document.getElementById('console-signout').onclick = async () => {
  if (await signOut(false)) await enterConsole();
};
document.getElementById('bookbar-signout').onclick = async () => {
  if (await signOut(false) && onConsoleScreen()) await enterConsole();
};
document.getElementById('bookbar-switch-account').onclick = () => switchAccount();
document.getElementById('bookbar-signin').onclick = () => enterConsole();
document.getElementById('signin-github-signout').onclick = () =>
  window.open('https://github.com/logout', '_blank', 'noopener');

/* --- the list --- */

function setWaitingCount(n) {
  const pill = document.getElementById('waiting-count');
  pill.textContent = n ? String(n) : '';
  pill.classList.toggle('hidden', !n);
}

async function loadConsole() {
  if (!WS.book) return openBookPicker();
  show('step-console');
  document.getElementById('console-who').textContent = 'Checking…';
  // Emptied first, so the previous book's list is never on screen under this
  // book's name while the new one loads.
  C.data = null;
  renderConsole();
  document.getElementById('console-who').textContent = 'Checking…';
  let data;
  try {
    data = await bookApi('/api/console/load', {});
  } catch (e) {
    await refreshWorkspace();
    return fail(e.message);
  }
  renderWorkspace(data.workspace);
  if (!WS.book || data.book.slug !== WS.book.slug) return loadConsole();
  C.data = data;
  renderConsole();
  document.getElementById('preview-status').textContent = '';
  checkDraftsPreview('console');
}

document.getElementById('console-refresh').onclick = async () => {
  await refreshWorkspace();
  await loadConsole();
};

document.getElementById('console-vault').onclick = async () => {
  try {
    const r = await api('/api/console/pick-vault', {});
    if (r.cancelled) return;
    if (r.error) return fail(r.error);
    renderWorkspace(r.workspace);
    await enterConsole();
  } catch (e) { fail(e.message); }
};

function renderConsole() {
  const d = C.data || {};
  document.getElementById('console-who').textContent =
    d.who ? ('Signed in as ' + d.who) : 'Signed in';

  const book = WS.book || {};
  document.getElementById('link-discussion').href = book.discussion_url || '#';
  document.getElementById('discussion-item').classList.toggle('hidden', !book.discussion_url);
  document.getElementById('link-history').href = book.history_url || '#';

  const vaultBtn = document.getElementById('console-vault');
  vaultBtn.textContent = WS.vault
    ? ('Vault: ' + WS.vault.name + ' — choose another')
    : ('Open the vault for ' + (book.title || 'this book'));

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
  // Something accepted but not yet sent to readers is still waiting on the
  // author, so it counts as waiting and the "nothing is waiting" line stays
  // away while it does.
  const pub = d.publish || null;
  setWaitingCount(sugs.length + drafts.length + (pub ? 1 : 0));
  document.getElementById('console-empty').classList.toggle(
    'hidden', !!(sugs.length || drafts.length || pub || (d.problems || []).length));

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
    // Accepted, but not yet made: the list is the author's to-do list.
    if (s.accepted) b.appendChild(el('span', 'tag', 'Accepted — yours to make'));
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

  C.publish = pub;
  document.getElementById('publish-block').classList.toggle('hidden', !pub);
  const pl = document.getElementById('publish-list');
  pl.innerHTML = '';
  if (pub) {
    document.getElementById('publish-count').textContent =
      '(' + (pub.change_count || 0) + ')';
    const li = el('li');
    const b = el('button');
    b.appendChild(el('strong', '', publishHeadline(pub)));
    if (pub.who && pub.who.length) {
      b.appendChild(el('span', 'who', 'from ' + pub.who.join(', ')));
    }
    b.appendChild(el('span', 'snip', pub.state_words || ''));
    b.onclick = () => openPublish(pub);
    li.appendChild(b);
    pl.appendChild(li);
  }

  const wl = document.getElementById('weekly-list');
  wl.innerHTML = '';
  (d.weekly || []).forEach(w => {
    const li = el('li');
    li.appendChild(el('strong', '', w.name + ': '));
    const state = w.state === 'ok' ? 'ran successfully'
      : w.state === 'failed' ? 'did not finish — worth telling the technical contact'
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
  // The plan takes several seconds to come back from the author's Mac, so
  // the wait is shown moving, with the seconds counted.
  const waiting = el('p', 'quiet');
  waiting.appendChild(el('span', 'spinner small'));
  const words = el('span', '', 'Looking at your chapter in the drafts area…');
  waiting.appendChild(words);
  box.appendChild(waiting);
  const started = Date.now();
  const ticking = setInterval(() => {
    words.textContent = 'Looking at your chapter in the drafts area… ' +
      Math.round((Date.now() - started) / 1000) + ' s';
  }, 1000);

  // Accept answers the plan on screen, so there is nothing to accept until
  // one is. Pressed while the plan was on its way, it would tell the reader
  // the author will do by hand what the tool is about to offer to do.
  const accept = document.getElementById('sug-accept');
  accept.disabled = true;
  accept.classList.remove('hidden');
  document.getElementById('sug-made').classList.add('hidden');
  let plan;
  try {
    plan = await bookApi('/api/console/plan', { number: s.number });
  } catch (e) {
    if (C.suggestion !== s) return;
    box.innerHTML = '';
    box.appendChild(el('p', 'notice bad', e.message));
    return;
  } finally {
    clearInterval(ticking);
  }
  // The author may have gone on to another suggestion meanwhile.
  if (C.suggestion !== s) return;
  C.plan = plan;
  renderPlan();
  accept.disabled = false;
}

function renderPlan() {
  const box = document.getElementById('sug-plan');
  const p = C.plan;
  box.innerHTML = '';

  // The change goes to the chapter in the drafts area. While a vault of this
  // book is open, it can go into the vault too: a book still published from
  // the author's folder shows readers what is there.
  const v = p.vault;
  const vaultTick = (v && v.can_apply) ? (() => {
    const lab = el('label', 'check confirm');
    const cb = el('input');
    cb.type = 'checkbox'; cb.id = 'sug-apply-vault'; cb.checked = !!v.suggested;
    lab.appendChild(cb);
    lab.appendChild(el('span', '',
      'Also make it in my vault “' + v.name + '”' +
      (v.suggested ? ' — readers of this book still see what is published from there.' : '.')));
    return lab;
  })() : null;

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
    lab.appendChild(el('span', '',
      'Make this change in the drafts area of ' + bookName() +
      ' (“' + p.branch + '”) as well as replying. The reply links to it.'));
    card.appendChild(lab);
    if (vaultTick) card.appendChild(vaultTick);
    box.appendChild(card);
    return;
  }

  const card = el('div', 'card');
  const accepted = C.suggestion && C.suggestion.accepted;
  card.appendChild(el('p', 'card-label',
    accepted ? 'You accepted this — it is yours to make' : 'This one is for you to do'));
  card.appendChild(el('p', '', p.reason));
  if (vaultTick) {
    card.appendChild(el('p', 'quiet', 'It can still be made in your vault:'));
    card.appendChild(vaultTick);
  } else if (accepted) {
    // Already thanked: what is left is to make the change, and then to
    // close it with a link to the change that made it.
    card.appendChild(el('p', 'quiet',
      'The reader has been thanked and told you will make the change. Make it under ' +
      'Chapters and send it to the drafts area, then press “I\'ve made the change”: ' +
      'the reader is sent a link to it and the suggestion is closed.'));
    document.getElementById('sug-accept').classList.add('hidden');
    document.getElementById('sug-made').classList.remove('hidden');
  } else {
    card.appendChild(el('p', 'quiet',
      'Accepting thanks them and tells them you will make the change by hand. It stays ' +
      'in this list, marked Accepted, until you have. Nothing in the chapter is changed — ' +
      'make the change yourself under Chapters.'));
  }
  box.appendChild(card);
}

document.getElementById('sug-back').onclick = () => show('step-console');
document.getElementById('sug-browser').onclick = () => {
  if (C.suggestion) window.open(C.suggestion.url, '_blank', 'noopener');
};

document.getElementById('sug-accept').onclick = async () => {
  const s = C.suggestion;
  if (!s || !C.plan) return;
  const cb = document.getElementById('sug-apply');
  const applyIt = !!(cb && cb.checked && C.plan && C.plan.can_apply);
  const vcb = document.getElementById('sug-apply-vault');
  const applyVault = !!(vcb && vcb.checked && C.plan && C.plan.vault && C.plan.vault.can_apply);
  const btn = document.getElementById('sug-accept');
  btn.disabled = true;
  try {
    const r = await bookApi('/api/console/accept', {
      number: s.number, apply: applyIt, apply_vault: applyVault,
      head: C.plan.head, plan_id: C.plan.plan_id,
    });
    if (r.moved) {
      // Nothing was changed or sent; here is the change as it would be now.
      C.plan = r.plan;
      renderPlan();
      return fail(r.message);
    }
    consoleDone(r.kept_open ? 'Accepted — now yours to make' : 'Accepted', r.steps);
  } catch (e) {
    fail(e.message);
  } finally { btn.disabled = false; }
};

// Closing a suggestion accepted by hand: the app names the change it found
// on the page since the acceptance, and the author confirms it is the one.
document.getElementById('sug-made').onclick = async () => {
  const s = C.suggestion;
  if (!s) return;
  const btn = document.getElementById('sug-made');
  btn.disabled = true;
  let found;
  try {
    found = await bookApi('/api/console/made', { number: s.number });
  } catch (e) {
    return fail(e.message);
  } finally { btn.disabled = false; }
  if (C.suggestion !== s) return;
  const c = found.change;
  const card = el('div', 'card after');
  card.appendChild(el('p', 'card-label', 'The latest change to ' + found.page));
  card.appendChild(el('p', '', '“' + c.message + '”'));
  card.appendChild(el('p', 'quiet', 'by ' + c.who + ' · ' + when(c.when)));
  card.appendChild(el('p', 'quiet',
    'If this is the change they suggested, the reader is thanked with a link to it ' +
    'and the suggestion is closed.'));
  const yes = el('button', 'yes', 'Yes, this is it — close the suggestion');
  yes.id = 'sug-made-confirm';
  yes.onclick = async () => {
    yes.disabled = true;
    try {
      const r = await bookApi('/api/console/made', { number: s.number, sha: c.sha });
      consoleDone('Closed, with a link to your change', r.steps);
    } catch (e) {
      fail(e.message);
    } finally { yes.disabled = false; }
  };
  card.appendChild(yes);
  const box = document.getElementById('sug-plan');
  box.appendChild(card);
};

document.getElementById('sug-decline').onclick = async () => {
  const s = C.suggestion;
  if (!s) return;
  const btn = document.getElementById('sug-decline');
  btn.disabled = true;
  try {
    const r = await bookApi('/api/console/decline', { number: s.number });
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
    detail = await bookApi('/api/console/draft-detail', { number: x.number });
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
    const r = await bookApi('/api/console/draft-accept', { number: x.number, title: x.title });
    consoleDone('Accepted', r.steps, r.warning);
  } catch (e) { fail(e.message); } finally { btn.disabled = false; }
};

document.getElementById('draft-decline').onclick = async () => {
  const x = C.draft;
  if (!x) return;
  const btn = document.getElementById('draft-decline');
  btn.disabled = true;
  try {
    const r = await bookApi('/api/console/draft-decline', { number: x.number });
    consoleDone('Declined', r.steps);
  } catch (e) { fail(e.message); } finally { btn.disabled = false; }
};

/* --- the drafts preview --- */
/* A book on the platform's builder has a preview of its drafts area, rebuilt
   each time the drafts move. The app compares what the preview was built from
   with the drafts area, and says "building" until they agree, then offers the
   link. Ten minutes without agreeing, it says so: nobody else would notice a
   build that failed. It asks again while the screen is showing. */

const P = { timer: null, where: null };
const PREVIEW_PLACES = {
  console: { step: 'step-console', box: 'preview-block',
             status: 'preview-status', link: 'preview-link' },
  import: { step: 'step-import-done', box: 'import-preview',
            status: 'import-preview-status', link: 'import-preview-link' },
};
const PREVIEW_AGAIN = { building: 20000, stale: 60000, unknown: 60000 };

function stopPreview() {
  if (P.timer) { clearTimeout(P.timer); P.timer = null; }
  P.where = null;
}

function drawDraftsPreview(where, p, checking) {
  const at = PREVIEW_PLACES[where];
  const status = document.getElementById(at.status);
  const link = document.getElementById(at.link);
  status.textContent = checking ? 'Checking the preview of the drafts area…'
    : p.words;
  status.className = !checking && p.state === 'stale' ? 'notice bad' : '';
  link.classList.toggle('hidden', !(p && p.url));
  if (p && p.url) { link.href = p.url; link.textContent = p.link; }
}

async function checkDraftsPreview(where) {
  const at = PREVIEW_PLACES[where];
  const book = WS.book;
  if (P.where !== where) stopPreview();
  if (P.timer) { clearTimeout(P.timer); P.timer = null; }
  document.getElementById(at.box).classList.toggle('hidden', !(book && book.drafts_preview));
  if (!book || !book.drafts_preview) return;
  P.where = where;
  const slug = book.slug;
  if (!document.getElementById(at.status).textContent) drawDraftsPreview(where, null, true);
  let p;
  try {
    const r = await bookApi('/api/console/preview', {});
    if (r.book !== slug) return;
    p = r.preview;
  } catch (e) {
    p = { state: 'unknown', words: 'Whether the preview is up to date couldn\'t be checked just now.',
          url: null };
  }
  // The author may have moved on, or to another book, while this was asked.
  if (P.where !== where || bookSlug() !== slug
      || document.getElementById(at.step).classList.contains('hidden')) return;
  if (!p) return document.getElementById(at.box).classList.add('hidden');
  drawDraftsPreview(where, p);
  const again = PREVIEW_AGAIN[p.state];
  if (again) P.timer = setTimeout(() => {
    P.timer = null;
    if (P.where === where && !document.getElementById(at.step).classList.contains('hidden')) {
      checkDraftsPreview(where);
    }
  }, again);
}

/* --- going live --- */

function publishHeadline(p) {
  const c = p.change_count || 0, n = p.page_count || 0;
  if (!c) return 'Nothing new is waiting to go to readers';
  const changes = c === 1 ? '1 change' : c + ' changes';
  const pages = n === 1 ? '1 page' : n + ' pages';
  return n ? (changes + ' to ' + pages + ', waiting to go to readers')
           : (changes + ' waiting to go to readers');
}

function openPublish(p) {
  C.publish = p;
  show('step-publish');
  renderPublish(p);
}

function renderPublish(p) {
  document.getElementById('publish-meta').textContent = p.open
    ? ('In line since ' + when(p.when))
    : 'Not in line for the live book yet';

  const body = document.getElementById('publish-body');
  body.innerHTML = '';

  const card = el('div', 'card');
  card.appendChild(el('p', 'card-label', 'What would go to readers'));
  card.appendChild(el('p', '', publishHeadline(p) + '.'));
  if (p.pages && p.pages.length) {
    card.appendChild(el('p', 'quiet', p.pages.join(' · ')));
  }
  if (p.who && p.who.length) {
    card.appendChild(el('p', 'quiet', 'Written by ' + p.who.join(', ') + '.'));
  }
  card.appendChild(el('p', 'quiet',
    'This is the whole of the drafts area, not only the last thing you accepted — anything published from the browser editor goes with it.'));
  body.appendChild(card);

  const state = el('p', 'notice' + (p.state === 'conflict' ? ' bad' : ''));
  state.textContent = p.state_words || '';
  body.appendChild(state);

  const go = document.getElementById('publish-go');
  const prep = document.getElementById('publish-prepare');
  prep.classList.toggle('hidden', !!p.open);
  go.classList.toggle('hidden', !p.open);
  go.disabled = !p.can_publish;
  document.getElementById('publish-browser').classList.toggle('hidden', !p.url);

  if (p.open && p.can_publish) {
    const lab = el('label', 'check confirm');
    const cb = el('input');
    cb.type = 'checkbox'; cb.id = 'publish-confirm';
    lab.appendChild(cb);
    lab.appendChild(el('span', '',
      'I have read what is above, and I want all of it to go to readers of ' +
      bookName() + ' now. This cannot be taken back from here.'));
    body.appendChild(lab);
  }
}

document.getElementById('publish-back').onclick = () => show('step-console');
document.getElementById('publish-browser').onclick = () => {
  if (C.publish && C.publish.url) window.open(C.publish.url, '_blank', 'noopener');
};

document.getElementById('publish-prepare').onclick = async () => {
  const btn = document.getElementById('publish-prepare');
  btn.disabled = true;
  try {
    const r = await bookApi('/api/console/publish-prepare', {});
    if (!r.publish) {
      return consoleDone('Nothing to send',
        ['The live book already has everything in the drafts area.']);
    }
    C.publish = r.publish;
    renderPublish(r.publish);
  } catch (e) { fail(e.message); } finally { btn.disabled = false; }
};

document.getElementById('publish-go').onclick = async () => {
  const p = C.publish;
  if (!p || !p.number) return;
  const cb = document.getElementById('publish-confirm');
  if (!cb || !cb.checked) {
    return fail('Please tick the box to confirm before publishing.');
  }
  const btn = document.getElementById('publish-go');
  btn.disabled = true;
  try {
    const r = await bookApi('/api/console/publish', { number: p.number, confirm: true });
    C.publish = null;
    consoleDone('Sent to the live book', r.steps);
  } catch (e) { fail(e.message); } finally { btn.disabled = false; }
};

/* --- afterwards --- */

function consoleDone(title, steps, warning) {
  document.getElementById('cdone-title').textContent = title;
  const list = document.getElementById('cdone-steps');
  list.innerHTML = '';
  (steps || []).filter(t => t).forEach(t => list.appendChild(el('li', '', t)));
  const warn = document.getElementById('cdone-warning');
  warn.textContent = warning || '';
  warn.classList.toggle('hidden', !warning);
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
  await refreshWorkspace();
  refreshAccount();
  refreshDeepseekOption();
  show(ENV.seen_welcome ? 'step-choose' : 'step-welcome');
  if (staleSeen) fail(STALE);
})();
