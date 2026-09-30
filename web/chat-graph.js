/* Keep the vault map in place while the camera visits each question's evidence. */
const ChatGraph = (() => {
  let vault, overviewData, loading, answer, data, view, mode = '', key = '', signature = '', sourcePrefix = '';
  let request = 0, vaultVersion = 0, activeJob = '', searched = new Set();
  const panel = () => $('#chat-graph-panel');
  const canvas = () => $('#dock-graph');
  const available = () => !panel().hidden;
  const graphIds = graph => graph.nodes.filter(n => n.citation).concat(graph.nodes.filter(n => !n.citation)).map(n => n.id);

  function combined(graph) {
    const nodes = new Map((overviewData?.nodes || []).map(node => [node.id, {...node, context_only: true, matched: false}]));
    const changed = new Set();
    for (const node of graph.nodes) {
      const current = nodes.get(node.id);
      if (current?.path && (current.path !== node.path || current.revision !== node.revision)) changed.add(node.id);
      nodes.set(node.id, {...node, context_only: false});
    }
    const edges = new Map();
    for (const edge of overviewData?.edges || []) if (!changed.has(edge.source) && !changed.has(edge.target)) {
      edges.set(JSON.stringify([edge.source, edge.target, edge.kind, edge.origin]), edge);
    }
    for (const edge of graph.edges) edges.set(JSON.stringify([edge.source, edge.target, edge.kind, edge.origin]), edge);
    return {...graph, nodes: [...nodes.values()], edges: [...edges.values()], journeyIds: graphIds(graph),
      context: true, total_nodes: nodes.size, omitted_nodes: 0, omitted_edges: 0, limit: null};
  }
  function highlight(node) { SourceCards.highlight(sourcePrefix, node?.citation, $('#page-ask')); }
  function controls() {
    const switcher = $('#dock-switch');
    switcher.hidden = mode === 'overview' && !answer;
    switcher.textContent = mode === 'overview' ? 'Answer evidence' : 'Whole vault';
  }
  function draw(snapshot) {
    if (!available() || $('#page-ask').hidden || !data) return;
    GraphView.dispose(canvas());
    view = GraphView.mount(canvas(), {...data, sourcePrefix, viewState: snapshot}, highlight);
  }
  function setGraph(graph, nextMode, nextKey, description, prefix = '') {
    const nextSignature = JSON.stringify([graph.nodes, graph.edges, graph.journeyIds, graph.scope, prefix]);
    const snapshot = view?.capture();
    data = graph; mode = nextMode; key = nextKey; sourcePrefix = prefix;
    $('#dock-context').textContent = description;
    controls();
    if (nextSignature !== signature || !view) {
      signature = nextSignature;
      GraphView.dispose(canvas()); view = null;
      draw(snapshot);
    }
    return view;
  }
  async function loadOverview(force = false) {
    if (overviewData && !force) return overviewData;
    if (loading) return loading;
    const currentVault = vaultVersion;
    const task = api('/graph?overview=true').then(graph => {
      if (currentVault === vaultVersion) overviewData = graph;
      return graph;
    }).finally(() => { if (loading === task) loading = null; });
    loading = task;
    return task;
  }
  async function overview(clearAnswer = false) {
    if (!available()) return;
    const ticket = ++request;
    activeJob = ''; searched.clear(); view?.stopJourney();
    if (clearAnswer) answer = null;
    if (!data) canvas().replaceChildren(el('p', 'Loading your vault map…', 'hint'));
    try {
      const graph = await loadOverview();
      if (ticket !== request || !available()) return;
      setGraph(graph, 'overview', 'overview', 'Whole vault · ask a question to follow its evidence.');
      view?.journey(graphIds(graph), {phase: 'overview'});
    } catch (error) {
      if (ticket === request) $('#dock-context').textContent = uiText(error.message);
    }
  }
  function rememberAnswer(result) { answer = result?.graph ? result : null; controls(); }
  function showAnswer(result, {animate = true} = {}) {
    if (!available()) return;
    if (!result?.graph) { overview(true); return; }
    const ticket = ++request, needsTour = !searched.size;
    activeJob = ''; answer = result; searched.clear();
    setGraph(combined(result.graph), 'answer', 'answer:' + result.id,
      `All evidence · ${result.question} · Current vault background with evidence saved at answer time`, 'source-' + result.id + '-');
    if (animate) {
      const ids = graphIds(result.graph);
      const finish = () => { if (ticket === request) view?.journey(ids, {phase: 'complete'}); };
      if (needsTour) view?.journey(ids, {phase: 'search', onEnd: finish});
      else finish();
    }
  }
  function showJob(job) {
    if (!job.graph?.nodes.length || !available() || activeJob !== job.id) return null;
    setGraph(combined(job.graph), 'job', 'job:' + job.id,
      `Evidence tour · ${job.question}`, 'source-' + job.id + '-');
    const targets = graphIds(job.graph).filter(id => !searched.has(id));
    if (targets.length) {
      targets.forEach(id => searched.add(id));
      view?.journey(targets, {phase: 'search'});
    }
    return view;
  }
  async function waiting(id, question) {
    if (!available()) return;
    const ticket = ++request;
    activeJob = id; searched.clear(); view?.stopJourney();
    $('#dock-context').textContent = `Searching your vault · ${question}`;
    try {
      const graph = await loadOverview(true);
      if (ticket !== request || activeJob !== id || !available()) return;
      setGraph(graph, 'waiting', 'job:' + id, `Searching your vault · ${question}`);
      view?.journey(graphIds(graph), {phase: 'overview'});
    } catch (error) {
      if (ticket === request) $('#dock-context').textContent = `Could not load the vault map · ${uiText(error.message)}`;
    }
  }
  function stop() { request++; activeJob = ''; view?.stopJourney(); }
  function focus(result, nodeId, {scroll = true} = {}) {
    if (!available() || !result?.graph) return false;
    if (key !== 'answer:' + result.id) showAnswer(result, {animate: false});
    view?.select(nodeId);
    if (scroll && matchMedia('(max-width:1100px)').matches) panel().scrollIntoView({behavior: 'smooth'});
    return true;
  }
  function sync(status) {
    const connected = !!status.vault;
    panel().hidden = !connected;
    $('#page-ask').classList.toggle('graph-dock', connected);
    if (!connected || vault !== status.vault) {
      stop(); vaultVersion++; vault = status.vault; answer = overviewData = loading = data = view = null;
      key = mode = signature = '';
      GraphView.dispose(canvas()); canvas().replaceChildren();
      if (connected) overview();
    }
  }
  function onPage(name) {
    if (name === 'ask' && data && !view) draw();
    else if (name !== 'ask') view?.stopJourney();
  }
  document.querySelector('#dock-switch').onclick = () =>
    mode === 'overview' && answer ? showAnswer(answer) : overview();
  return {sync, overview, showAnswer, rememberAnswer, showJob, waiting, stop, focus, onPage, available};
})();
