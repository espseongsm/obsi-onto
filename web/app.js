const $ = selector => document.querySelector(selector);
let token, status, currentRun, refreshTimer, graphRequest = 0;
const titles = {ask: 'Ask your notes', graph: 'Knowledge search', library: 'Notes & index', review: 'Review relationships', settings: 'Vault settings'};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function toast(message) {
  $('#toast').textContent = uiText(message);
  $('#toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('#toast').hidden = true, 6500);
}
async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, {
    method, headers: {'Content-Type': 'application/json', 'X-Obsi-Token': token || ''},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json();
  if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? uiText(data.detail) : 'Please check your input.'); error.status = response.status; throw error; }
  return data;
}
async function action(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}
function page(name) {
  if (status && !status.vault && name !== 'settings') name = 'ask';
  if (status && !status.generation_enabled && name === 'review') name = 'ask';
  document.querySelectorAll('.page').forEach(node => node.hidden = node.id !== 'page-' + name);
  GraphView.visibility();
  ChatGraph.onPage(name);
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === name));
  $('#page-name').textContent = name === 'ask' && status && !status.vault ? 'Connect vault' : titles[name];
  document.title = 'Obsi Onto · ' + $('#page-name').textContent;
  if (name === 'review') loadCandidates().catch(error => toast(error.message));
  if (name === 'graph') loadGraph().catch(error => toast(error.message));
  if (name === 'settings') {
    $('#vault-path').value = status?.vault || '';
    $('#excludes').value = (status?.excludes || []).join('\n');
    $('#daily-dates').checked = status?.daily_filename_dates ?? true;
    $('#debounce').value = status?.debounce || 2;
    $('#interval').value = (status?.reconcile_seconds || 1800) / 60;
  }
}
function requireVault() {
  if (status?.vault) return true;
  page('ask');
  $('#onboarding').scrollIntoView({block: 'center'});
  $('#quick-path').focus({preventScroll: true});
  return false;
}
function renderAccess(data) {
  const connected = !!data.vault, generation = !!data.generation_enabled;
  $('#page-ask').classList.toggle('needs-vault', !connected);
  $('#question').disabled = !connected;
  $('#ask-button').disabled = !connected;
  $('#new-chat').disabled = !connected;
  for (const button of document.querySelectorAll('.nav')) {
    button.disabled = !connected && !['ask', 'settings'].includes(button.dataset.page) ||
      !generation && button.dataset.page === 'review';
    button.title = button.disabled ? !connected ? 'Connect a vault first.' : 'Connect an LLM to use this feature.' : '';
  }
  titles.ask = generation ? 'Ask your notes' : 'Evidence search';
  $('#ask-nav-label').textContent = connected ? titles.ask : 'Connect vault';
  if (!$('#page-ask').hidden) {
    $('#page-name').textContent = connected ? titles.ask : 'Connect vault';
    document.title = 'Obsi Onto · ' + $('#page-name').textContent;
  }
  $('#ask-action-label').textContent = generation ? 'Ask' : 'Find evidence';
  $('#question-form>label').textContent = generation ? 'What would you like to know?' : 'What evidence are you looking for?';
  $('#question').placeholder = generation ? 'Why did we change direction on Project B?' : 'Enter a name or phrase from your notes.';
  const heading = $('.intro h1');
  heading.replaceChildren(document.createTextNode(generation ? 'Your notes grow.' : 'Find it in your notes.'),
    el('br'), document.createTextNode(generation ? 'Your ideas connect.' : 'Follow the evidence.'));
  $('.intro p').textContent = generation ? 'From work decisions to personal insights. Ask your notes and explore the evidence behind each answer.' :
    'Find original passages and explore how your notes connect.';
  $('#search-mode-notice').hidden = !connected || generation;
  $('#search-mode-reason').textContent = uiText(data.generation_reason) || 'No LLM is connected.';
  $('#page-graph>.hint').textContent = `Search names and text. For source evidence and semantic search, use ${titles.ask}.`;
}
function renderStatus(data) {
  const first = !status, disconnected = !!status?.vault && !data.vault;
  status = data;
  renderAccess(data);
  ChatGraph.sync(data);
  renderScenarios(data);
  $('#onboarding').hidden = !!data.vault;
  $('#nav-count').textContent = data.counts.notes;
  $('#stat-notes').textContent = data.counts.ready;
  $('#stat-links').textContent = data.counts.links;
  $('#vault-title').textContent = data.vault ? data.vault.split('/').at(-1) : 'No vault connected';
  $('#vault-caption').textContent = data.vault || 'Your notes, with their original sources.';
  const pending = data.counts.notes - data.counts.ready;
  $('#sync-badge').textContent = uiText(data.task) || (pending ? `${pending} pending or unavailable` : data.vault ? 'Index up to date' : 'Not connected');
  $('#model-mode').textContent = data.generation_enabled ? `AI answers · ${data.generation_model} · Cited sources` : 'Evidence search · Original passages and sources';
  $('#scan-info').textContent = `${uiText(data.task) || 'Idle'} · Last checked ${data.last_scan ? new Date(data.last_scan).toLocaleString('en') : 'Never'}\n` +
    `Last scan: ${data.metrics.files_checked || 0} files / ${data.metrics.body_reads || 0} content reads / ${data.metrics.embedding_calls || 0} embedding calls / ${data.metrics.seconds || 0}s · CPU ${data.metrics.cpu_seconds || 0}s`;
  $('#scan-errors').replaceChildren();
  for (const error of [data.error, ...data.scan_errors].filter(Boolean)) $('#scan-errors').append(el('p', uiText(error)));
  $('#notes-table').replaceChildren();
  for (const note of data.notes) {
    const row = el('tr');
    for (const value of [note.path, {ready: 'Ready', pending: 'Updating', error: 'Pending / error'}[note.state], note.revision,
      uiText(note.error) || (note.vector_state === 'pending' ? 'Waiting for the semantic search model' : 'Text and semantic indexes ready')]) row.append(el('td', value));
    $('#notes-table').append(row);
  }
  $('#quality').replaceChildren();
  if (!data.quality.length) $('#quality').append(el('div', 'No links need attention.', 'empty'));
  for (const link of data.quality) $('#quality').append(el('p', `${link.path}:${link.line} → ${link.raw} · ${link.status}`));
  $('#model-info').replaceChildren(el('p', `${data.embedding_ready ? '● Ready' : '○ Not ready'} · ${data.embedding_model}`),
    el('p', data.generation_enabled ? `Answer model: ${data.generation_model}` : 'Evidence search · LLM not in use'),
    el('p', `Embeddings: ${data.embedding_external ? 'External API enabled' : 'Local'} / Answers: ${data.generation_enabled ? data.generation_external ? 'External API enabled' : 'Local' : 'Off'}`, 'hint'),
    el('p', `Index location: ${data.data_dir}`, 'hint'));
  if (!data.generation_enabled) $('#model-info').append(el('p', uiText(data.generation_reason) || 'Evidence and knowledge search work without an LLM.', 'hint'));
  $('#model-info').append(el('p', 'Follow “LLM API key setup” in the README. Set the endpoint, API key and model in .env, then restart the server.', 'hint'));
  PrivacyUI.render(data);
  $('#prepare-model').textContent = data.embedding_ready ? 'Local model ready' : 'Prepare local semantic model';
  $('#prepare-model').disabled = data.embedding_ready || data.task === 'Preparing local model';
  if (!data.vault && (first || disconnected)) requireVault();
  else if (!data.generation_enabled && !$('#page-review').hidden) page('ask');
}
async function refresh() {
  clearTimeout(refreshTimer);
  try { renderStatus(await api('/status')); }
  catch (error) { toast('Cannot connect to the local server. Check that it is running.'); }
  refreshTimer = setTimeout(refresh, status?.task || ['pending', 'generating'].includes(status?.suggestions?.state) && status?.vault ? 1800 : 10000);
}
async function history() {
  await ChatUI.history();
}
function renderAnswer(result, historical = false, area) {
  currentRun = result;
  GraphView.dispose(area);
  area.replaceChildren();
  $('#suggestions').open = false;
  const header = el('div', undefined, 'answer-header');
  header.append(el('h2', result.evidence.length ? result.generated ? 'Answer' : 'Search results' : uiText(result.message)),
    el('span', result.generated ? 'Based on your notes' : 'Source excerpts', 'answer-label'));
  area.append(header);
  let answerGraph, requestedGraphNode;
  const graphPanel = el('details', undefined, 'chat-graph');
  function focusSource(node) {
    SourceCards.highlight('source-' + result.id + '-', node?.citation, area);
  }
  function focusEvidence(item, scroll = false) {
    if (!result.graph || ChatGraph.focus(result, 'section/' + item.id, {scroll})) return;
    requestedGraphNode = 'section/' + item.id;
    graphPanel.open = true;
    answerGraph?.select(requestedGraphNode);
    if (scroll) graphPanel.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  if (result.graph) {
    const canvas = el('div');
    graphPanel.append(el('summary', '3D evidence graph'), canvas);
    graphPanel.open = true;
    answerGraph = GraphView.watch(canvas, {...result.graph, sourcePrefix: 'source-' + result.id + '-'}, focusSource);
  }
  const layout = el('div', undefined, 'result-layout');
  const response = el('div', undefined, 'response');
  const sources = SourceCards.create(result, focusEvidence);
  if (!result.sentences.length) response.append(el('p', 'Try a broader scope or date range, or use a name found in your notes.'));
  for (const sentence of result.generated ? result.sentences : result.sentences.slice(0, 3)) {
    const p = el('p', sentence.text);
    for (const id of sentence.citations) {
      const a = el('a', id, 'citation'); a.href = '#source-' + result.id + '-' + id;
      a.onclick = event => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (SourceCards.open('source-' + result.id + '-' + id, a)) event.preventDefault();
      }; p.append(a);
    }
    response.append(p);
  }
  if (!result.generated && result.sentences.length > 3) response.append(el('p',
    `See Sources for the remaining ${result.sentences.length - 3} passages.`, 'hint'));
  layout.append(response); area.append(layout);
  const support = el('div', undefined, 'answer-support');
  support.append(sources);
  if (result.graph) support.append(graphPanel);
  else support.append(el('p', 'This older answer has no graph snapshot. Ask again to save one.', 'hint'));
  area.append(support);
  if (result.clarifications?.length) {
    const confirmations = el('section', undefined, 'info-box');
    confirmations.append(el('h3', 'Your clarification · Basis for this answer'));
    for (const c of result.clarifications) confirmations.append(el('p',
      `${c.conflict.question} → ${{a: 'Source A', b: 'Source B', both: 'Different contexts', defer: 'Deferred', explain: 'Your explanation'}[c.choice]}${c.explanation ? ' · ' + c.explanation : ''}`));
    response.append(confirmations);
  }
  const warnings = el('ul', undefined, 'warnings');
  for (const warning of result.warnings) warnings.append(el('li', uiText(warning)));
  const trace = el('details', undefined, 'trace');
  trace.append(el('summary', 'Search & verification' + (result.warnings.length ? ` · ${result.warnings.length} notices` : '')));
  trace.append(el('p', `Text search${result.mode !== 'lexical' ? ' + Semantic search' : ''}${result.mode === 'hybrid' ? ' + Relationship search' : ''} → Source verification → ${result.evidence.length} sources`, 'hint'));
  if (warnings.childElementCount) trace.append(warnings);
  trace.append(el('code', JSON.stringify({plan: {...result.plan, date_basis: uiText(result.plan?.date_basis)}, shacl: result.validation.conforms}, null, 2)));
  support.append(trace);
  if (historical) support.append(el('p', `Saved answer · ${new Date(result.created_at).toLocaleString('en')}\nBased on the notes at that time; the current source may differ.`, 'hint answer-meta'));
  if (status?.generation_enabled && !historical && result.evidence.length) {
    const extract = el('button', 'Suggest relationships from these sources', 'answer-extract');
    extract.onclick = () => action(extract, async () => { const r = await api(`/runs/${result.id}/extract`, 'POST'); toast(`Created ${r.created.length} suggestions. Open Review relationships to review them.`); });
    support.append(extract);
  }
  if (!historical) area.scrollIntoView({behavior: 'smooth', block: 'start'});
}
async function loadGraph() {
  const request = ++graphRequest;
  const container = $('#vault-graph');
  GraphView.dispose(container);
  container.replaceChildren(el('p', 'Loading connections…', 'hint'));
  const data = await api('/graph?q=' + encodeURIComponent($('#graph-query').value.trim()));
  if (request === graphRequest) GraphView.mount(container, data);
}
async function loadCandidates() {
  const candidates = await api('/candidates');
  $('#candidates').replaceChildren();
  if (!candidates.length) $('#candidates').append(el('div', 'No suggested relationships yet. Connect an LLM, then suggest relationships from answer sources.', 'empty'));
  for (const candidate of candidates) {
    const card = el('article', undefined, 'panel candidate');
    card.append(el('h2', `${candidate.kind === 'Claim' ? 'Claim' : 'Activity'} → ${candidate.topic}`),
      el('p', `${candidate.path}:${candidate.start}–${candidate.end} · Event date ${candidate.event_date || 'Unknown'} · Status ${candidate.activity_state}`, 'hint'),
      el('blockquote', candidate.quote));
    if (candidate.status === 'pending') {
      const actions = el('div', undefined, 'actions');
      for (const [label, accept] of [['Accept relationship', true], ['Reject', false]]) {
        const button = el('button', label, accept ? 'primary' : '');
        button.onclick = () => action(button, async () => { await api(`/candidates/${candidate.id}/review`, 'POST', {accept}); await loadCandidates(); });
        actions.append(button);
      }
      card.append(actions);
    } else card.append(el('p', candidate.status === 'accepted' ? 'Reviewed · Included in relationship search' : 'Rejected suggestion', 'hint'));
    $('#candidates').append(card);
  }
}
document.querySelectorAll('.nav').forEach(button => button.onclick = () => page(button.dataset.page));
$('#graph-form').onsubmit = event => { event.preventDefault(); action(event.submitter, loadGraph); };
$('#graph-reset').onclick = event => action(event.currentTarget, async () => { $('#graph-query').value = ''; await loadGraph(); });
document.querySelectorAll('.pick-folder').forEach(button => button.onclick = async () => {
  const input = document.getElementById(button.dataset.target);
  const buttons = document.querySelectorAll('.pick-folder');
  buttons.forEach(node => node.disabled = true);
  const label = button.textContent;
  button.textContent = 'Choosing…';
  try {
    const result = await api('/vault/pick-folder', 'POST', {initial_path: input.value.trim()});
    if (result.path !== null) {
      input.value = result.path;
      input.focus();
      toast('Folder selected. Choose Connect vault or Save connection to apply it.');
    }
  } catch (error) { toast(error.message); }
  finally { buttons.forEach(node => node.disabled = false); button.textContent = label; }
});
$('#quick-connect').onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => {
    await api('/vault', 'POST', {path: $('#quick-path').value, excludes: []}); toast('Vault connected. Indexing available notes.'); await refresh();
  });
};
$('#demo').onclick = event => action(event.currentTarget, async () => {
  await api('/demo', 'POST'); toast('Sample vault connected.'); await refresh();
});
$('#question-form').onsubmit = event => {
  event.preventDefault();
  if (!requireVault()) return;
  action($('#ask-button'), async () => {
    await QueryUI.start({question: $('#question').value.trim(), domain: $('#domain').value,
      start: $('#start').value || null, end: $('#end').value || null, generate: !!status.generation_enabled});
  });
};
$('#question').onkeydown = event => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); $('#question-form').requestSubmit(); }
};
$('#new-chat').onclick = () => { if (requireVault()) ChatUI.fresh(); };
$('#model-help').onclick = () => { page('settings'); $('#model-info').scrollIntoView({block: 'center'}); };
$('#settings-form').onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => {
    await api('/vault', 'POST', {path: $('#vault-path').value, excludes: $('#excludes').value.split('\n').map(s => s.trim()).filter(Boolean),
      daily_filename_dates: $('#daily-dates').checked}); toast('Vault settings saved. Suggestions will update after indexing.'); await refresh();
  });
};
for (const [id, deep] of [['scan', false], ['deep', true]]) $('#' + id).onclick = event => action(event.currentTarget, async () => {
  await api('/scan', 'POST', {deep}); toast(deep ? 'Deep scan scheduled.' : 'Metadata scan scheduled.'); await refresh();
});
$('#prepare-model').onclick = event => action(event.currentTarget, async () => {
  await api('/models/prepare', 'POST'); toast('Preparing the public embedding model. The first download may take a few minutes.'); await refresh();
});
$('#tuning-form').onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => {
    await api('/settings', 'POST', {debounce: Number($('#debounce').value), reconcile_seconds: Number($('#interval').value) * 60}); toast('Refresh settings saved.');
  });
};
PrivacyUI.bind();
$('#delete-data').onclick = event => action(event.currentTarget, async () => {
  if (!confirm('Delete the local index, conversations and relationship reviews? Your original notes will be preserved.')) return;
  await api('/data', 'DELETE'); ChatUI.fresh(); GraphView.dispose($('#vault-graph'));
  $('#vault-graph').replaceChildren();
  currentRun = null; graphRequest++; $('#suggestions').open = true;
  await refresh(); await history(); toast('Local index and history deleted.'); page('ask');
});
document.addEventListener('visibilitychange', () => {
  GraphView.visibility();
  if (document.hidden) clearTimeout(refreshTimer); else refresh();
});
(async () => {
  $('#launch-help').hidden = true;
  $('.sidebar').hidden = false;
  $('main').hidden = false;
  try {
    token = (await api('/session')).token; await refresh(); await history();
    if (status?.vault) { await ChatUI.restore(); await QueryUI.restore(); }
  }
  catch (error) { toast(error.message); }
})();
