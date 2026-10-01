/* Actual server stages and source-backed clarification; all content stays plain text. */
const QueryUI = (() => {
  const terminal = new Set(['completed', 'cancelled', 'failed', 'interrupted', 'stale']);
  const labels = {queued: 'Queued', retrieving: 'Finding evidence', verifying: 'Verifying sources',
    checking_conflicts: 'Comparing conflicts', awaiting_user: 'Awaiting clarification', generating: 'Writing answer',
    finalizing: 'Rechecking sources', completed: 'Complete', cancelled: 'Cancelled', failed: 'Failed',
    interrupted: 'Interrupted', stale: 'Sources changed'};
  let active, timer, version = '', serial = 0, lastSeq = 0, lastState = '';
  const root = () => document.querySelector('#query-progress');
  function stop() { clearTimeout(timer); serial++; }
  function clear({keepGraph = false} = {}) {
    if (!keepGraph) ChatGraph.stop();
    stop(); active = null; version = ''; lastState = ''; sessionStorage.removeItem('obsi-job');
    GraphView.dispose(root()); root().replaceChildren(); root().hidden = true;
  }
  function button(label, work) {
    const b = el('button', label); b.type = 'button';
    b.onclick = () => action(b, work); return b;
  }
  function show(job) {
    const elapsed = $('#job-elapsed');
    if (elapsed) elapsed.textContent = `${Math.max(0, Math.floor((Date.now() - Date.parse(elapsed.dataset.started)) / 1000))}s elapsed`;
    if (job.unchanged || version === `${job.id}:${job.seq}`) return;
    lastSeq = job.seq;
    version = `${job.id}:${job.seq}`;
    lastState = job.state;
    if (!terminal.has(job.state)) ChatUI.pending(job);
    const area = root(); area.hidden = false; GraphView.dispose(area); area.replaceChildren();
    const head = el('div', undefined, 'answer-header');
    const label = job.state === 'generating' && !status?.generation_enabled ? 'Organizing evidence' : labels[job.state] || job.state;
    head.append(el('h2', label), el('span', 'Live progress', 'answer-label'));
    area.append(head, el('p', job.question, 'hint'));
    if (!terminal.has(job.state) && job.state !== 'awaiting_user') { const elapsed = el('p', 'Working…', 'hint'); elapsed.id = 'job-elapsed'; elapsed.dataset.started = job.created_at; area.append(elapsed); }
    const trace = el('details'); trace.open = !terminal.has(job.state);
    trace.append(el('summary', 'Search and verification steps'));
    const steps = el('ol', undefined, 'job-steps');
    for (const e of job.events) {
      const time = new Date(e.created_at).toLocaleTimeString('en');
      steps.append(el('li', `${time} · ${uiText(e.message)}${e.verified_count !== undefined ? ` (${e.verified_count} ${e.verified_count === 1 ? 'source' : 'sources'})` : ''}`));
    }
    trace.append(steps); area.append(trace);
    const controls = el('div', undefined, 'actions');
    if (!terminal.has(job.state)) controls.append(button('Cancel question', async () => {
      const generation = serial;
      const cancelled = await api(`/query-jobs/${job.id}/cancel`, 'POST');
      if (generation !== serial || active !== job.id) return;
      show(cancelled); stop(); await pending();
    }));
    if (['stale', 'interrupted', 'failed'].includes(job.state)) controls.append(button('Edit question', async () => {
      $('#question').value = job.question; $('#question').focus();
    }));
    area.append(controls);
    if (job.state === 'completed' && job.result) {
      const generation = serial;
      ChatUI.complete(job.result).then(() => { if (generation === serial) return refresh(); }).catch(error => toast(uiText(error.message)));
      return;
    }
    if (terminal.has(job.state)) ChatGraph.stop();
    const graph = !terminal.has(job.state) ? ChatGraph.showJob(job) : null;
    if (job.confirmation) confirmCard(area, job, graph);
    else if (job.evidence.length && !terminal.has(job.state)) {
      const found = el('details'); found.append(el('summary', `Verified sources (${job.evidence.length})`));
      for (const item of job.evidence) found.append(el('p', `${item.citation} · ${item.path}:${item.start}–${item.end}`));
      area.append(found);
    }
    for (const warning of job.warnings || []) area.append(el('p', uiText(warning), 'hint'));
    if (job.state === 'awaiting_user') area.append(el('p', 'You can respond later. This question is saved while note indexing continues.', 'hint'));
  }
  function confirmCard(area, job, graph) {
    const question = job.confirmation;
    const card = el('section', undefined, 'clarification-card');
    card.append(el('strong', 'Possible conflict · clarification needed'), el('h3', question.question), el('p', question.reason));
    const pair = el('div', undefined, 'conflict-sources');
    for (const key of ['a', 'b']) {
      const side = question[key], source = job.evidence.find(e => e.citation === side.citation);
      const item = el('article', undefined, 'source-card');
      item.id = 'source-' + job.id + '-' + side.citation;
      item.append(el('h4', `${key.toUpperCase()} · ${source.path}`),
        el('p', `Recorded ${source.record_date || 'unknown'} · Lines ${source.start}–${source.end} · Version ${source.revision}`, 'hint'),
        el('blockquote', side.quote));
      const link = el('a', 'Open in Obsidian ↗'); link.href = source.uri; item.append(link);
      if (graph) item.append(button('View in graph', async () => graph.select('section/' + source.id)));
      pair.append(item);
    }
    card.append(pair);
    const form = el('form'), options = el('fieldset'); options.append(el('legend', 'Choose which source to use for this answer'));
    for (const [value, label] of [['a', 'Use source A'], ['b', 'Use source B'],
      ['both', 'Keep both: different contexts'], ['explain', 'Explain the context']]) {
      const row = el('label', undefined, 'clarification-option'), radio = el('input');
      radio.type = 'radio'; radio.name = 'choice'; radio.value = value; radio.required = true;
      row.append(radio, document.createTextNode(label)); options.append(row);
    }
    const explanation = el('textarea'); explanation.maxLength = 2000; explanation.rows = 3;
    explanation.placeholder = 'Add any useful context. Required if you choose “Explain the context”.';
    explanation.setAttribute('aria-label', 'Clarification details');
    const submit = el('button', 'Confirm and continue', 'primary'); submit.type = 'submit';
    const defer = button('Defer · use a partial source-based answer', async () => send('defer'));
    form.append(options, explanation, el('p', 'Your choice and explanation apply only to this answer. Your original notes remain unchanged.', 'hint'), submit, defer);
    // The same request ID is reused after a lost response; a changed answer gets a new ID.
    let lastBody, requestId;
    async function send(choice) {
      const body = {question_id: question.id, version: question.version, choice, explanation: explanation.value.trim()};
      if (choice === 'explain' && !body.explanation) throw new Error('Please enter your clarification.');
      const signature = JSON.stringify(body);
      if (signature !== lastBody) { lastBody = signature; requestId = crypto.randomUUID(); }
      submit.disabled = defer.disabled = true;
      try {
        const generation = serial;
        const updated = await api(`/query-jobs/${job.id}/clarifications`, 'POST', {...body, request_id: requestId});
        if (generation !== serial || active !== job.id) return;
        show(updated);
        if (!terminal.has(updated.state)) await follow(job.id);
      } finally { submit.disabled = defer.disabled = false; }
    }
    form.onsubmit = event => {
      event.preventDefault(); const selected = form.querySelector('input[name=choice]:checked');
      if (selected) action(submit, () => send(selected.value));
    };
    card.append(form); area.append(card);
  }
  async function poll(id, generation) {
    if (id !== active || generation !== serial || document.hidden) return;
    try {
      const job = await api('/query-jobs/' + id + '?after=' + lastSeq);
      if (generation !== serial) return;
      show(job);
      if (terminal.has(job.state) || job.state === 'awaiting_user') { await pending(); return; }
    } catch (error) { toast(uiText(error.message)); if (error.status === 404 || error.status === 403) { clear(); return; } }
    if (generation === serial) timer = setTimeout(() => poll(id, generation), 750);
  }
  async function follow(id, {restoring = false} = {}) {
    stop(); active = id; version = ''; lastSeq = 0; sessionStorage.setItem('obsi-job', id);
    const generation = serial, job = await api('/query-jobs/' + id);
    if (generation !== serial) return;
    if (restoring && terminal.has(job.state)) { clear({keepGraph: true}); return; }
    page('ask'); await ChatUI.select(job.conversation_id);
    if (generation !== serial) return;
    await ChatGraph.waiting(id, job.question);
    if (generation !== serial) return;
    show(job);
    if (terminal.has(job.state) || job.state === 'awaiting_user') await pending();
    else timer = setTimeout(() => poll(id, generation), 750);
  }
  async function start(request) {
    if (active && lastState && !terminal.has(lastState)) throw new Error('Finish the current question or start a new chat.');
    const generation = serial;
    const job = await api('/query-jobs', 'POST', {...request, conversation_id: ChatUI.startId(), request_id: crypto.randomUUID()});
    if (generation !== serial) return;
    $('#question').value = '';
    await follow(job.id);
  }
  async function pending() {
    const target = $('#pending-questions'); if (!target) return;
    target.replaceChildren();
    for (const job of await api('/query-jobs')) target.append(button(
      `${labels[job.state] || job.state} · ${new Date(job.created_at).toLocaleString('en')}`,
      () => follow(job.id)));
  }
  async function restore() {
    const generation = serial;
    await pending();
    if (generation !== serial) return;
    const id = sessionStorage.getItem('obsi-job');
    if (id) await follow(id, {restoring: true});
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else if (active) poll(active, serial);
  });
  return {start, restore, pending, clear};
})();
