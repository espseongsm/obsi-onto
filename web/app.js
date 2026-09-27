const $ = selector => document.querySelector(selector);
let token, status, currentRun, refreshTimer, graphRequest = 0;
const titles = {ask: '기록에 질문하기', graph: '지식 그래프', library: '노트와 색인', review: '관계 검토', settings: '볼트 설정'};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function toast(message) {
  $('#toast').textContent = message;
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
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '입력 내용을 확인하세요.');
  return data;
}
async function action(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}
function page(name) {
  document.querySelectorAll('.page').forEach(node => node.hidden = node.id !== 'page-' + name);
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === name));
  $('#page-name').textContent = titles[name];
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
function renderStatus(data) {
  status = data;
  renderScenarios(data);
  $('#onboarding').hidden = !!data.vault;
  $('#nav-count').textContent = data.counts.notes;
  $('#stat-notes').textContent = data.counts.ready;
  $('#stat-links').textContent = data.counts.links;
  $('#vault-title').textContent = data.vault ? data.vault.split('/').at(-1) : '연결된 볼트가 없습니다';
  $('#vault-caption').textContent = data.vault || '노트의 원문과 출처를 함께 보관합니다.';
  const pending = data.counts.notes - data.counts.ready;
  $('#sync-badge').textContent = data.task || (pending ? `${pending}개 대기·오류` : data.vault ? '색인 최신' : '연결 대기');
  $('#model-mode').textContent = data.generation_enabled ? `생성 답변 · ${data.generation_model} · 문장별 출처 확인` : '원문 근거 모드 · 생성 모델 꺼짐';
  $('#scan-info').textContent = `${data.task || '대기 중'} · 마지막 확인 ${data.last_scan ? new Date(data.last_scan).toLocaleString() : '아직 없음'}\n` +
    `최근 대조: 파일 ${data.metrics.files_checked || 0}개 / 본문 읽기 ${data.metrics.body_reads || 0}회 / 임베딩 ${data.metrics.embedding_calls || 0}회 / ${data.metrics.seconds || 0}초 · CPU ${data.metrics.cpu_seconds || 0}초`;
  $('#scan-errors').replaceChildren();
  for (const error of [data.error, ...data.scan_errors].filter(Boolean)) $('#scan-errors').append(el('p', error));
  $('#notes-table').replaceChildren();
  for (const note of data.notes) {
    const row = el('tr');
    for (const value of [note.path, {ready: '최신', pending: '갱신 중', error: '대기 / 오류'}[note.state], note.revision,
      note.error || (note.vector_state === 'pending' ? '의미 검색 모델 준비 대기' : '단어·의미 색인 완료')]) row.append(el('td', value));
    $('#notes-table').append(row);
  }
  $('#quality').replaceChildren();
  if (!data.quality.length) $('#quality').append(el('div', '현재 확인이 필요한 링크가 없습니다.', 'empty'));
  for (const link of data.quality) $('#quality').append(el('p', `${link.path}:${link.line} → ${link.raw} · ${link.status}`));
  $('#model-info').replaceChildren(el('p', `${data.embedding_ready ? '● 준비됨' : '○ 준비 전'} · ${data.embedding_model}`),
    el('p', data.generation_enabled ? `생성 모델: ${data.generation_model}` : '생성 모델: 꺼짐'),
    el('p', `임베딩: ${data.embedding_external ? '외부 전송 허용' : '로컬'} / 생성: ${data.generation_enabled ? data.generation_external ? '외부 전송 허용' : '로컬' : '꺼짐'}`, 'hint'),
    el('p', `색인 위치: ${data.data_dir}`, 'hint'));
  if (data.generation_enabled && data.generation_external) {
    $('#model-info').append(el('p', '답변 생성 시 질문과 검색된 근거 문단을 외부 API에 전달합니다.', 'hint'));
    $('#model-info').append(el('p', data.suggestions_external ?
      '예상 질문 생성: 외부 전송 허용 · 제외 규칙을 적용한 최대 24개 노트의 제목·기록일·일부 본문을 전달합니다.' :
      '예상 질문 생성: 외부 전송 꺼짐 · 노트 제목으로 기본 질문을 만듭니다. 본문을 사용한 자동 생성은 별도 전송 허용이 필요합니다.', 'hint'));
  }
  $('#prepare-model').textContent = data.embedding_ready ? '로컬 모델 준비 완료' : '로컬 의미 검색 모델 준비';
  $('#prepare-model').disabled = data.embedding_ready || data.task === '로컬 모델 준비 중';
}
async function refresh() {
  clearTimeout(refreshTimer);
  try { renderStatus(await api('/status')); }
  catch (error) { toast('로컬 서버에 연결할 수 없습니다. 실행 상태를 확인하세요.'); }
  refreshTimer = setTimeout(refresh, status?.task || ['pending', 'generating'].includes(status?.suggestions?.state) && status?.vault ? 1800 : 10000);
}
async function history() {
  const runs = await api('/runs');
  $('#history').replaceChildren();
  if (!runs.length) $('#history').append(el('p', '아직 질문이 없습니다.', 'hint'));
  for (const run of runs) {
    const button = el('button', run.question);
    button.title = run.question;
    button.onclick = () => action(button, async () => { page('ask'); renderAnswer(await api('/runs/' + run.id), true); });
    $('#history').append(button);
  }
}
function renderAnswer(result, historical = false) {
  currentRun = result;
  const area = $('#answer');
  area.hidden = false;
  area.replaceChildren();
  $('#suggestions').open = false;
  const header = el('div', undefined, 'answer-header');
  header.append(el('h2', result.message), el('span', historical ? '당시 근거 스냅샷' : result.generated ? '생성 답변' : '원문 근거', 'answer-label'));
  area.append(header);
  if (historical) area.append(el('p', `이 답변은 ${new Date(result.created_at).toLocaleString()} 당시 기록입니다. 현재 원문과 다를 수 있습니다.`, 'hint'));
  area.append(el('p', result.question, 'hint'));
  let answerGraph;
  const graphPanel = el('section');
  function focusSource(node) {
    area.querySelectorAll('.source-card').forEach(card => card.classList.toggle('selected-source', card.id === 'source-' + node?.citation));
  }
  if (result.graph) {
    area.append(graphPanel);
    answerGraph = GraphView.mount(graphPanel, result.graph, focusSource);
  } else area.append(el('p', '이전 버전의 질문에는 그래프 스냅샷이 없습니다. 다시 질문하면 함께 저장됩니다.', 'hint'));
  const layout = el('div', undefined, 'result-layout');
  const response = el('div', undefined, 'response');
  const sources = el('div');
  if (!result.sentences.length) response.append(el('p', '검색 범위나 날짜를 넓히거나, 노트에 사용한 대상 이름으로 다시 질문해 보세요.'));
  for (const sentence of result.sentences) {
    const p = el('p', sentence.text);
    for (const id of sentence.citations) {
      const a = el('a', id, 'citation'); a.href = '#source-' + id;
      a.onclick = () => answerGraph?.select('section/' + id.slice(1)); p.append(a);
    }
    response.append(p);
  }
  for (const item of result.evidence) {
    const card = el('article', undefined, 'source-card'); card.id = 'source-' + item.citation;
    const meta = el('div', undefined, 'source-meta');
    for (const value of [item.citation, ...item.routes, `기록일 ${item.record_date || '미상'}`]) meta.append(el('span', value));
    card.append(meta, el('h3', item.path + (item.heading ? ' › ' + item.heading : '')),
      el('small', `${item.start}–${item.end}줄 · 버전 ${item.revision} · ${item.hash.slice(0, 10)}`, 'hint'), el('pre', item.text));
    const open = el('a', 'Obsidian에서 원문 열기 ↗'); open.href = item.uri; card.append(open);
    if (answerGraph) {
      const show = el('button', '그래프에서 이 문단 보기 ↑', 'show-in-graph');
      show.onclick = () => { answerGraph.select('section/' + item.id); graphPanel.scrollIntoView({behavior: 'smooth', block: 'start'}); };
      card.append(show);
    }
    sources.append(card);
  }
  layout.append(response, sources); area.append(layout);
  const warnings = el('ul', undefined, 'warnings');
  for (const warning of result.warnings) warnings.append(el('li', warning)); area.append(warnings);
  const trace = el('details', undefined, 'trace');
  trace.append(el('summary', '검색 범위와 검증 정보'));
  trace.append(el('p', `단어 검색${result.mode !== 'lexical' ? ' + 의미 검색' : ''}${result.mode === 'hybrid' ? ' + 관계 조회' : ''} → 원문 검증 → ${result.evidence.length}개 근거`, 'hint'));
  trace.append(el('code', JSON.stringify({plan: result.plan, shacl: result.validation.conforms}, null, 2)));
  area.append(trace);
  if (status?.generation_enabled && !historical && result.evidence.length) {
    const extract = el('button', '이 근거에서 의미 관계 후보 제안');
    extract.onclick = () => action(extract, async () => { const r = await api(`/runs/${result.id}/extract`, 'POST'); toast(`${r.created.length}개 후보를 만들었습니다. 관계 검토에서 확인하세요.`); });
    area.append(extract);
  }
  area.scrollIntoView({behavior: 'smooth', block: 'start'});
}
async function loadGraph() {
  const request = ++graphRequest;
  const container = $('#vault-graph');
  container.replaceChildren(el('p', '현재 연결을 불러오는 중…', 'hint'));
  const data = await api('/graph?q=' + encodeURIComponent($('#graph-query').value.trim()));
  if (request === graphRequest) GraphView.mount(container, data);
}
async function loadCandidates() {
  const candidates = await api('/candidates');
  $('#candidates').replaceChildren();
  if (!candidates.length) $('#candidates').append(el('div', '아직 관계 후보가 없습니다. 생성 모델을 설정한 뒤 답변의 근거에서 후보를 제안할 수 있습니다.', 'empty'));
  for (const candidate of candidates) {
    const card = el('article', undefined, 'panel candidate');
    card.append(el('h2', `${candidate.kind === 'Claim' ? '판단' : '활동'} → ${candidate.topic}`),
      el('p', `${candidate.path}:${candidate.start}–${candidate.end} · 사건일 ${candidate.event_date || '미상'} · 상태 ${candidate.activity_state}`, 'hint'),
      el('blockquote', candidate.quote));
    if (candidate.status === 'pending') {
      const actions = el('div', undefined, 'actions');
      for (const [label, accept] of [['관계 확인', true], ['거절', false]]) {
        const button = el('button', label, accept ? 'primary' : '');
        button.onclick = () => action(button, async () => { await api(`/candidates/${candidate.id}/review`, 'POST', {accept}); await loadCandidates(); });
        actions.append(button);
      }
      card.append(actions);
    } else card.append(el('p', candidate.status === 'accepted' ? '검토 완료 · 관계 탐색에 포함' : '거절한 후보', 'hint'));
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
  button.textContent = '선택 중…';
  try {
    const result = await api('/vault/pick-folder', 'POST', {initial_path: input.value.trim()});
    if (result.path !== null) {
      input.value = result.path;
      input.focus();
      toast('폴더 경로를 채웠습니다. 볼트 연결 또는 연결 설정 저장을 눌러 적용하세요.');
    }
  } catch (error) { toast(error.message); }
  finally { buttons.forEach(node => node.disabled = false); button.textContent = label; }
});
$('#quick-connect').onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => {
    await api('/vault', 'POST', {path: $('#quick-path').value, excludes: []}); toast('볼트를 연결했습니다. 읽을 수 있는 노트를 색인합니다.'); await refresh();
  });
};
$('#demo').onclick = event => action(event.currentTarget, async () => {
  await api('/demo', 'POST'); toast('가상 샘플 볼트를 연결했습니다.'); await refresh();
});
$('#question-form').onsubmit = event => {
  event.preventDefault(); action($('#ask-button'), async () => {
    $('#ask-button').textContent = '근거 확인 중…';
    try {
      const result = await api('/ask', 'POST', {question: $('#question').value.trim(), domain: $('#domain').value,
        start: $('#start').value || null, end: $('#end').value || null});
      renderAnswer(result); await history();
    } finally { $('#ask-button').textContent = '근거 찾기 ↗'; }
  });
};
$('#question').onkeydown = event => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); $('#question-form').requestSubmit(); }
};
$('#settings-form').onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => {
    await api('/vault', 'POST', {path: $('#vault-path').value, excludes: $('#excludes').value.split('\n').map(s => s.trim()).filter(Boolean),
      daily_filename_dates: $('#daily-dates').checked}); toast('볼트 설정을 저장했습니다. 변경된 설정에 맞춰 색인 후 예상 질문을 갱신합니다.'); await refresh();
  });
};
for (const [id, deep] of [['scan', false], ['deep', true]]) $('#' + id).onclick = event => action(event.currentTarget, async () => {
  await api('/scan', 'POST', {deep}); toast(deep ? '정밀 검사를 예약했습니다.' : '메타데이터 대조를 예약했습니다.'); await refresh();
});
$('#prepare-model').onclick = event => action(event.currentTarget, async () => {
  await api('/models/prepare', 'POST'); toast('공개 임베딩 모델을 준비합니다. 최초 다운로드는 몇 분 걸릴 수 있습니다.'); await refresh();
});
$('#tuning-form').onsubmit = event => {
  event.preventDefault(); action(event.submitter, async () => {
    await api('/settings', 'POST', {debounce: Number($('#debounce').value), reconcile_seconds: Number($('#interval').value) * 60}); toast('갱신 설정을 저장했습니다.');
  });
};
$('#delete-data').onclick = event => action(event.currentTarget, async () => {
  if (!confirm('로컬 색인·질문 기록·관계 검토를 삭제할까요? 원본 노트는 보존됩니다.')) return;
  await api('/data', 'DELETE'); $('#answer').replaceChildren(); $('#answer').hidden = true; $('#vault-graph').replaceChildren();
  currentRun = null; graphRequest++; $('#suggestions').open = true;
  await refresh(); await history(); toast('로컬 색인과 기록을 삭제했습니다.'); page('ask');
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearTimeout(refreshTimer); else refresh();
});
(async () => {
  try { token = (await api('/session')).token; await refresh(); await history(); }
  catch (error) { toast(error.message); }
})();
