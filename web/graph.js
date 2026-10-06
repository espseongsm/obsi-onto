/* Shared 3D graph UI for current knowledge and saved answer evidence. */
const GraphView = (() => {
  const kinds = {Note: 'Note', Section: 'Passage', Topic: 'Topic', Tag: 'Tag', Claim: 'Reviewed claim', Activity: 'Reviewed activity'};
  const origins = {structure: 'document structure', explicit_link: 'explicit link', explicit_tag: 'explicit tag', frontmatter: 'note property', user_review: 'user reviewed', clarification: 'answer clarification'};
  const relations = {contains: 'contains passage', taggedWith: 'tagged with', about: 'about topic', records: 'records reviewed statement', linksTo: 'links to', conflict_candidate: 'needs clarification'};
  const views = new Map(), watchers = new Map();
  function disposeMounted(root) {
    for (const [container, scene] of views) if (container === root || root.contains(container)) {
      scene?.dispose(); views.delete(container);
    }
  }
  function dispose(root) {
    for (const [container, watcher] of watchers) if (container === root || root.contains(container)) watcher.dispose();
    disposeMounted(root);
  }
  function watch(container, data, onSelect = () => {}) {
    data = GraphLayout.bounded(data);
    dispose(container);
    const disclosure = container.closest('details');
    let near = false, mounted, snapshot = data.viewState, selected = '', disposed = false;
    container.style.minHeight = '420px';
    container.replaceChildren(el('p', 'The 3D graph loads as you scroll into view.', 'hint'));
    function update() {
      if (disposed) return;
      if (near && (!disclosure || disclosure.open)) {
        if (mounted) return;
        const restoreId = selected;
        mounted = mount(container, {...data, viewState: snapshot}, node => {
          selected = node?.id || ''; onSelect(node);
        });
        container.style.minHeight = '';
        if (restoreId) mounted.select(restoreId);
      } else if (mounted) {
        snapshot = mounted.capture();
        container.style.minHeight = Math.max(420, container.offsetHeight) + 'px';
        disposeMounted(container); mounted = null;
        container.replaceChildren(el('p', 'The 3D graph resumes when it comes into view.', 'hint'));
      }
    }
    const observer = new IntersectionObserver(entries => { near = entries[0].isIntersecting; update(); }, {rootMargin: '200px'});
    observer.observe(container); disclosure?.addEventListener('toggle', update);
    const watcher = {
      select(id) { if (!disposed) { selected = id; mounted?.select(id); } },
      dispose() {
        disposed = true; observer.disconnect(); disclosure?.removeEventListener('toggle', update);
        disposeMounted(container); mounted = null; watchers.delete(container); container.style.minHeight = '';
      }
    };
    watchers.set(container, watcher); return watcher;
  }
  function visibility() {
    for (const [container, scene] of views) scene?.visible(!document.hidden && !container.closest('[hidden]'));
  }
  function mount(container, data, onSelect = () => {}) {
    data = GraphCategories.decorate(GraphLayout.bounded(data));
    disposeMounted(container); container.replaceChildren(); container.classList.add('graph-view');
    container.classList.remove('has-selection');
    const nodes = data.nodes, edges = data.edges, byId = new Map(nodes.map(node => [node.id, node]));
    const heading = el('div', undefined, 'graph-heading');
    const title = el('div'); title.append(el('small', 'YOUR KNOWLEDGE, CONNECTED', 'graph-eyebrow'),
      el('h3', data.scope === 'answer' ? 'Evidence constellation' : 'Knowledge constellation'),
      el('p', data.context ? 'Follow this answer’s evidence across your vault.' : data.scope === 'answer' ? 'From an answer to the records behind it.' : 'Explore how your notes connect.', 'graph-caption'));
    const count = el('div', undefined, 'graph-count');
    for (const [value, label] of [[nodes.length, 'nodes'], [edges.length, 'links']]) {
      const metric = el('span'); metric.append(el('strong', String(value)), el('small', label)); count.append(metric);
    }
    heading.append(title, count); container.append(heading);
    if (!nodes.length) {
      container.append(el('p', data.query ? 'No matching nodes. Try a shorter name or phrase.' : 'No connections to show. Connect a vault or ask a question about your notes.', 'empty'));
      return {select() {}, capture() {}, journey() {}, stopJourney() {}};
    }
    const toolbar = el('div', undefined, 'graph-toolbar'), picker = el('select');
    picker.setAttribute('aria-label', 'Select a graph node'); picker.append(new Option('Choose a note, passage, or idea…', ''));
    for (const node of nodes) picker.append(new Option(`${kinds[node.kind]} · ${node.label.slice(0, 45)}${node.start ? ` (${node.path}:${node.start})` : ''}`, node.id));
    const controls = el('div', undefined, 'graph-controls'); toolbar.append(picker, controls);
    const body = el('div', undefined, 'graph-body'), stage = el('div', undefined, 'graph-stage');
    const hud = el('div', undefined, 'graph-hud');
    const mode = el('span', 'Drag to rotate', 'graph-motion'), focusLabel = el('span', 'All connections', 'graph-focus');
    hud.append(mode, focusLabel);
    const help = el('div', 'Select a node to explore its connections.', 'graph-gesture');
    stage.append(hud, help);
    const inspector = el('aside', undefined, 'graph-inspector'); inspector.setAttribute('aria-live', 'polite');
    inspector.setAttribute('aria-label', 'Selected record and connections'); inspector.hidden = true;
    inspector.onkeydown = event => {
      if (event.key === 'Escape') { event.preventDefault(); select(''); picker.focus({preventScroll: true}); }
    };
    body.append(stage, toolbar, inspector);
    const legend = el('div', undefined, 'graph-legend');
    legend.setAttribute('aria-label', 'Knowledge area color legend');
    legend.append(el('strong', 'Knowledge areas', 'graph-legend-title'));
    for (const {id, label} of GraphCategories.items) {
      const count = nodes.filter(node => node.category === id).length;
      if (count) legend.append(el('span', `${label} ${count}`, 'graph-legend-item category-' + id));
    }
    if (edges.some(e => e.kind === 'conflict_candidate')) legend.append(el('span', 'Orange dashed line · discrepancy to review', 'graph-conflict-key'));
    const guide = el('button', 'Graph guide ↗', 'graph-guide'); guide.type = 'button';
    guide.setAttribute('aria-expanded', 'false');
    guide.onclick = () => {
      if (picker.value) select('');
      inspector.hidden = !inspector.hidden;
      guide.setAttribute('aria-expanded', String(!inspector.hidden));
    };
    legend.append(guide);
    container.append(body, legend);
    if (data.omitted_nodes || data.omitted_edges || data.background_omitted_nodes || data.background_omitted_edges || data.evidence_omitted_nodes) {
      const message = `Showing ${nodes.length} of ${data.total_nodes || nodes.length} nodes` +
        (data.omitted_nodes ? ` · ${data.omitted_nodes} nodes omitted` : '') +
        (data.omitted_edges ? ` · ${data.omitted_edges} links omitted${data.edge_count_scope === 'displayed_nodes' ? ' between shown nodes' : ''}` : '') +
        (data.background_omitted_nodes ? ` · vault overview omitted ${data.background_omitted_nodes} nodes` : '') +
        (data.background_omitted_edges ? ` · vault overview omitted ${data.background_omitted_edges} links` : '') +
        (data.evidence_omitted_nodes ? ` · evidence view omitted ${data.evidence_omitted_nodes} nodes` : '');
      const notice = el('p', message, 'graph-limit'), search = el('button', 'Search omitted records');
      search.type = 'button'; search.onclick = () => searchRecords(); notice.append(search); container.append(notice);
    }
    function searchRecords(query = '') {
      const input = document.querySelector('#graph-query');
      if (!input) return;
      input.value = query.slice(0, 200); page('graph'); input.focus();
    }
    let scene;
    try { scene = GraphScene.mount(stage, data, select); }
    catch (error) {
      stage.dataset.renderer = 'unavailable';
      stage.append(el('p', '3D rendering is unavailable in this browser. Use the node list to explore connections and source text.', 'graph-unavailable'));
    }
    views.set(container, scene);
    function button(label, title, work, toggle) {
      const control = el('button', label); control.type = 'button'; control.setAttribute('aria-label', title); control.title = title;
      if (toggle !== undefined) control.setAttribute('aria-pressed', String(toggle));
      control.onclick = () => work(control); controls.append(control); return control;
    }
    for (const [label, title, work] of [
      ['−', 'Zoom out', () => scene?.zoom(1.2)], ['+', 'Zoom in', () => scene?.zoom(.8)],
      ['⤢', 'Reset graph view', () => scene?.reset()]
    ]) button(label, title, work).disabled = !scene;
    const rotate = button('⟳', 'Auto-rotate graph', control => {
      const enabled = control.getAttribute('aria-pressed') !== 'true';
      control.setAttribute('aria-pressed', String(enabled)); scene?.rotate(enabled);
      mode.textContent = enabled ? 'Slow rotation' : 'Drag to rotate';
    }, false);
    rotate.disabled = !scene;
    button('Labels', 'Show node labels', control => {
      const enabled = control.getAttribute('aria-pressed') !== 'true';
      control.setAttribute('aria-pressed', String(enabled)); scene?.labels(enabled);
    }, true).disabled = !scene;
    const journeyIds = (data.journeyIds || []).filter(id => byId.has(id));
    let journeyObserver;
    if (journeyIds.length && scene) {
      const journeys = el('div', undefined, 'graph-journey-controls');
      journeys.setAttribute('aria-label', 'Evidence tour');
      toolbar.append(journeys);
      const replay = el('button', 'Follow evidence'), overview = el('button', 'All evidence'), stop = el('button', 'Stop tour');
      for (const control of [replay, overview, stop]) { control.type = 'button'; journeys.append(control); control.disabled = !scene; }
      replay.title = 'The camera visits evidence locations. This is not the LLM’s reasoning sequence.';
      replay.onclick = () => journey(journeyIds, {phase: 'search'});
      overview.onclick = () => journey(journeyIds, {phase: 'complete'});
      stop.onclick = () => scene?.stopJourney();
      function updateJourney() {
        const state = stage.querySelector('canvas')?.dataset;
        const running = state?.journey === 'running';
        stop.hidden = !running; replay.disabled = !scene || running;
        mode.textContent = running ? state.phase === 'complete' ? 'Framing all evidence' : 'Evidence tour' :
          state?.journey === 'complete' ? 'All evidence' : 'Drag to rotate';
      }
      journeyObserver = new MutationObserver(updateJourney);
      journeyObserver.observe(stage, {subtree: true, attributes: true, attributeFilter: ['data-journey', 'data-phase']});
      updateJourney();
    }
    const clear = button('Clear', 'Clear node selection', () => select('')); clear.hidden = true;
    picker.onchange = () => select(picker.value);
    function select(id) {
      const focus = GraphLayout.focus(nodes, edges, id), selected = focus.selected;
      picker.value = selected ? id : ''; scene?.select(focus);
      container.classList.toggle('has-selection', !!selected); clear.hidden = !selected;
      inspector.hidden = !selected; guide.setAttribute('aria-expanded', String(!!selected));
      focusLabel.textContent = selected ? `${kinds[selected.kind]} · ${focus.neighbors.size - 1} direct connections` : focus.seeds.size ? `${focus.seeds.size} search matches` : 'All connections';
      inspector.replaceChildren();
      const close = el('button', '×', 'graph-inspector-close'); close.type = 'button'; close.setAttribute('aria-label', 'Close node details');
      close.onclick = () => { select(''); picker.focus({preventScroll: true}); }; inspector.append(close);
      if (!selected) {
        inspector.append(el('span', 'EXPLORE YOUR NOTES', 'inspector-eyebrow'), el('h4', 'From one note\nto the next idea'),
          el('p', 'Select a node or choose a record from the list to see its direct connections and supporting evidence.', 'hint'),
          el('div', 'Drag · rotate\nScroll / + − · zoom\nRight-drag / two fingers · pan\nArrow keys · rotate / Home · fit all', 'graph-instructions'),
          el('p', 'Colors group nodes into knowledge areas using rules based on tags, topics, names, and folders. Original node types remain visible in the list and details. These visual categories do not change stored relationships or establish semantic similarity. Selection keeps the category color and adds a gold ring and highlighted connections. Rings also mark search matches. Lines represent actual links, properties, document structure, and reviewed relationships. Orange dashed lines mark discrepancies to review. 3D positions and distances help navigation.', 'hint'));
      } else {
        const category = GraphCategories.items.find(item => item.id === selected.category);
        inspector.append(el('span', category.label, 'graph-kind-badge category-' + category.id),
          el('span', kinds[selected.kind], 'graph-node-kind hint'), el('h4', selected.label),
          el('p', selected.categoryReason, 'graph-category-reason hint'));
        if (data.context) inspector.append(el('p', selected.context_only ? 'Background record from the current vault' : data.scope === 'answer' ? 'Evidence saved with this answer' : 'Evidence found for this question', 'hint'));
        if (selected.path) inspector.append(el('p', `${selected.path}${selected.start ? `:${selected.start}–${selected.end}` : ''} · revision ${selected.revision} · recorded ${selected.record_date || 'unknown'}`, 'hint'));
        if (selected.routes?.length) inspector.append(el('p', `Found by: ${selected.routes.map(uiText).join(' + ')}`, 'hint'));
        if (selected.event_date || selected.activity_state) inspector.append(el('p', `Event date: ${selected.event_date || 'unknown'} · status: ${selected.activity_state}`, 'hint'));
        if (selected.excerpt) inspector.append(el('pre', selected.excerpt));
        inspector.append(el('strong', 'Connected records', 'graph-relation-title'));
        const relationList = el('ul', undefined, 'graph-relations');
        for (const edge of edges.filter(e => e.source === id || e.target === id)) {
          const other = byId.get(edge.source === id ? edge.target : edge.source);
          if (!other) continue;
          const link = el('button', `${edge.source === id ? '→' : '←'} ${other.label} · ${relations[edge.kind] || edge.label || edge.kind} (${origins[edge.origin] || edge.origin})`);
          link.type = 'button'; link.onclick = () => select(other.id);
          const li = el('li'); li.append(link); relationList.append(li);
        }
        inspector.append(relationList);
        if (selected.uri) { const open = el('a', 'Open source in Obsidian ↗'); open.href = selected.uri; inspector.append(open); }
        const explore = el('button', selected.kind === 'Note' ? 'Explore this note' : 'Search related records');
        explore.type = 'button'; explore.onclick = () => searchRecords(selected.path || selected.label); inspector.append(explore);
        if (data.scope === 'answer' && selected.citation) {
          const source = el('a', `View source ${selected.citation} ↓`); source.href = '#' + (data.sourcePrefix || 'source-') + selected.citation; inspector.append(source);
        }
      }
      onSelect(selected);
    }
    function journey(ids, options) {
      select(''); rotate.setAttribute('aria-pressed', 'false');
      scene?.journey(ids, options);
    }
    if (scene) {
      const disposeScene = scene.dispose;
      scene.dispose = () => { journeyObserver?.disconnect(); disposeScene(); };
    }
    select(''); visibility();
    return {select, capture: () => scene?.capture(), journey, stopJourney: () => scene?.stopJourney()};
  }
  window.addEventListener('pagehide', () => dispose(document));
  return {mount, watch, dispose, visibility};
})();
