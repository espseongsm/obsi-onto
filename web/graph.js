/* Local SVG rendering. Layout runs once per view; navigation does not call a model. */
const GraphView = (() => {
  const ns = 'http://www.w3.org/2000/svg';
  const kinds = {Note: '노트', Section: '원문 문단', Topic: '주제', Tag: '태그', Claim: '검토된 판단', Activity: '검토된 활동'};
  const origins = {structure: '문서 구조', explicit_link: '명시 링크', explicit_tag: '명시 태그', frontmatter: '노트 속성', user_review: '사용자 검토'};
  let sequence = 0;
  function svgNode(tag, attrs, text) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function positions(nodes, edges) {
    const points = new Map(nodes.map((node, i) => {
      const angle = i * 2.39996, radius = 35 + 220 * Math.sqrt(i / Math.max(1, nodes.length));
      return [node.id, {x: 460 + Math.cos(angle) * radius * 1.5, y: 270 + Math.sin(angle) * radius, dx: 0, dy: 0}];
    }));
    const values = [...points.values()];
    for (let step = 0; step < 150; step++) {
      for (const p of values) { p.dx = (460 - p.x) * .008; p.dy = (270 - p.y) * .008; }
      values.forEach((p, i) => values.slice(i + 1).forEach(q => {
        const x = p.x - q.x, y = p.y - q.y, d = Math.max(1, Math.hypot(x, y));
        const force = Math.min(18, 4000 / (d * d));
        p.dx += x / d * force; p.dy += y / d * force;
        q.dx -= x / d * force; q.dy -= y / d * force;
      }));
      for (const edge of edges) {
        const a = points.get(edge.source), b = points.get(edge.target);
        if (!a || !b) continue;
        const x = b.x - a.x, y = b.y - a.y, d = Math.max(1, Math.hypot(x, y));
        const force = (d - (edge.kind === 'contains' ? 96 : 135)) * .025;
        a.dx += x / d * force; a.dy += y / d * force;
        b.dx -= x / d * force; b.dy -= y / d * force;
      }
      const cooling = 1 - step / 190;
      for (const p of values) {
        p.x = Math.max(75, Math.min(845, p.x + Math.max(-18, Math.min(18, p.dx)) * cooling));
        p.y = Math.max(40, Math.min(485, p.y + Math.max(-18, Math.min(18, p.dy)) * cooling));
      }
    }
    return points;
  }
  function mount(container, data, onSelect = () => {}) {
    container.replaceChildren();
    container.classList.add('graph-view');
    const nodes = data.nodes, edges = data.edges;
    const byId = new Map(nodes.map(node => [node.id, node]));
    const heading = el('div', undefined, 'graph-heading');
    heading.append(el('h3', data.scope === 'answer' ? '이 답변의 근거 그래프' : '기록의 연결 지도'),
      el('span', `${nodes.length}개 노드 · ${edges.length}개 연결`, 'hint'));
    container.append(heading);
    container.append(el('p', data.scope === 'answer'
      ? '검색으로 찾은 문단과 근거의 연결입니다. 문단·판단을 선택해 맥락을 따라가세요.'
      : '현재 색인의 일부를 표시합니다. 이름이나 본문으로 찾은 뒤 연결을 탐색하세요.', 'graph-caption'));
    if (data.omitted_nodes || data.omitted_edges) container.append(el('p',
      `범위 내 ${data.total_nodes}개 중 ${nodes.length}개 표시 · 최대 ${data.limit}개, 주변 2단계로 제한${data.omitted_edges ? ` · 연결 ${data.omitted_edges}개 생략` : ''}`, 'hint graph-limit'));
    if (!nodes.length) {
      container.append(el('p', data.query ? '일치하는 노드가 없습니다. 짧은 대상 이름으로 찾아보세요.' : '표시할 연결이 없습니다. 볼트를 연결하거나 근거가 있는 질문을 해보세요.', 'empty'));
      return {select() {}};
    }
    const legend = el('div', undefined, 'graph-legend');
    for (const [kind, label] of Object.entries(kinds)) {
      if (nodes.some(node => node.kind === kind)) legend.append(el('span', label, 'kind-' + kind));
    }
    legend.append(el('span', '초록 테두리: 검색 일치', 'legend-match'), el('span', '주황 테두리: 선택', 'legend-picked'));
    container.append(legend);
    const toolbar = el('div', undefined, 'graph-toolbar');
    const picker = el('select'); picker.setAttribute('aria-label', '그래프 노드 선택');
    picker.append(new Option('노트·문단·판단 선택…', ''));
    for (const node of nodes) picker.append(new Option(`${kinds[node.kind]} · ${node.label.slice(0, 45)}${node.start ? ` (${node.path}:${node.start})` : ''}`, node.id));
    toolbar.append(picker);
    const controls = el('div', undefined, 'graph-controls');
    const svg = svgNode('svg', {viewBox: '0 0 920 540', role: 'group', 'aria-label': '노트와 근거의 연결 그래프'});
    const markerId = 'graph-arrow-' + ++sequence;
    const marker = svgNode('marker', {id: markerId, viewBox: '0 0 10 10', refX: 18, refY: 5, markerWidth: 5, markerHeight: 5, orient: 'auto-start-reverse'});
    marker.append(svgNode('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#9aa992'}));
    const defs = svgNode('defs'); defs.append(marker); svg.append(defs);
    const coords = positions(nodes, edges), lines = [], groups = new Map();
    for (const edge of edges) {
      const a = coords.get(edge.source), b = coords.get(edge.target);
      const line = svgNode('line', {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'graph-edge edge-' + edge.kind, 'marker-end': `url(#${markerId})`});
      line.append(svgNode('title', {}, `${byId.get(edge.source).label} → ${byId.get(edge.target).label} · ${edge.label} (${origins[edge.origin]})`));
      svg.append(line); lines.push([edge, line]);
    }
    for (const node of nodes) {
      const p = coords.get(node.id);
      const group = svgNode('g', {transform: `translate(${p.x} ${p.y})`, tabindex: 0, role: 'button', 'aria-label': `${kinds[node.kind]}: ${node.label}${node.start ? ` · ${node.path}:${node.start}` : ''}`, 'aria-pressed': 'false'});
      const label = node.label.length > 16 ? node.label.slice(0, 15) + '…' : node.label;
      group.append(svgNode('circle', {r: node.kind === 'Note' ? 12 : 8}),
        svgNode('text', {y: node.kind === 'Note' ? 30 : 26, 'text-anchor': 'middle'}, label),
        svgNode('title', {}, `${kinds[node.kind]} · ${node.label}${node.path ? '\n' + node.path : ''}${node.start ? `:${node.start}–${node.end}` : ''}`));
      group.onclick = () => select(node.id);
      group.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node.id); } };
      groups.set(node.id, group); svg.append(group);
    }
    const inspector = el('div', undefined, 'graph-inspector');
    inspector.setAttribute('aria-live', 'polite');
    let view = {x: 0, y: 0, width: 920, height: 540}, drag;
    function drawView() { svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`); }
    function zoom(factor) {
      const width = Math.max(320, Math.min(1840, view.width * factor)), height = width * 540 / 920;
      view.x += (view.width - width) / 2; view.y += (view.height - height) / 2;
      view.width = width; view.height = height; drawView();
    }
    for (const [label, title, work] of [
      ['−', '그래프 축소', () => zoom(1.25)], ['+', '그래프 확대', () => zoom(.8)],
      ['전체 보기', '그래프 위치 초기화', () => { view = {x: 0, y: 0, width: 920, height: 540}; drawView(); }],
      ['선택 해제', '그래프 선택 해제', () => select('')]
    ]) {
      const button = el('button', label); button.type = 'button'; button.setAttribute('aria-label', title); button.onclick = work; controls.append(button);
    }
    toolbar.append(controls); container.append(toolbar, svg, inspector);
    svg.onpointerdown = event => {
      if (event.target.closest('.graph-node')) return;
      drag = {x: event.clientX, y: event.clientY, view: {...view}};
      svg.setPointerCapture(event.pointerId);
    };
    svg.onpointermove = event => {
      if (!drag) return;
      const scale = view.width / svg.getBoundingClientRect().width;
      view.x = drag.view.x - (event.clientX - drag.x) * scale;
      view.y = drag.view.y - (event.clientY - drag.y) * scale;
      drawView();
    };
    svg.onpointerup = svg.onpointercancel = () => { drag = null; };
    picker.onchange = () => select(picker.value);
    function select(id) {
      const selected = byId.get(id);
      picker.value = selected ? id : '';
      const focus = new Set(selected ? [id] : nodes.filter(n => n.matched).map(n => n.id));
      const neighborhood = new Set(focus);
      for (const edge of edges) if (focus.has(edge.source) || focus.has(edge.target)) {
        neighborhood.add(edge.source); neighborhood.add(edge.target);
      }
      for (const node of nodes) {
        const group = groups.get(node.id);
        group.setAttribute('class', ['graph-node', 'kind-' + node.kind, node.matched ? 'matched' : '', node.id === id ? 'picked' : '', focus.size && !neighborhood.has(node.id) ? 'dim' : ''].join(' '));
        group.setAttribute('aria-pressed', String(node.id === id));
      }
      for (const [edge, line] of lines) {
        const highlighted = focus.has(edge.source) || focus.has(edge.target);
        line.classList.toggle('highlighted', highlighted);
        line.classList.toggle('dim', focus.size > 0 && !highlighted);
      }
      inspector.replaceChildren();
      if (!selected) inspector.append(el('strong', '연결의 근거를 확인하세요'), el('p', '노드를 선택하면 직접 연결된 이웃이 강조됩니다. 빈 공간을 끌어 이동하고 + / −로 확대·축소할 수 있습니다. 선은 명시 링크·속성·문서 구조·검토된 관계이며, 배치 거리는 의미 유사도 점수가 아닙니다.', 'hint'));
      else {
        inspector.append(el('strong', `${kinds[selected.kind]} · ${selected.label}`));
        if (selected.path) inspector.append(el('p', `${selected.path}${selected.start ? `:${selected.start}–${selected.end}` : ''} · 버전 ${selected.revision} · 기록일 ${selected.record_date || '미상'}`, 'hint'));
        if (selected.routes?.length) inspector.append(el('p', `검색 경로: ${selected.routes.join(' + ')}`, 'hint'));
        if (selected.event_date || selected.activity_state) inspector.append(el('p', `사건일 ${selected.event_date || '미상'} · 활동 상태 ${selected.activity_state}`, 'hint'));
        if (selected.excerpt) inspector.append(el('pre', selected.excerpt));
        const relations = el('ul', undefined, 'graph-relations');
        for (const edge of edges.filter(e => e.source === id || e.target === id)) {
          const other = byId.get(edge.source === id ? edge.target : edge.source);
          const button = el('button', `${edge.source === id ? '→' : '←'} ${other.label} · ${edge.label} (${origins[edge.origin]})`);
          button.onclick = () => select(other.id);
          const li = el('li'); li.append(button); relations.append(li);
        }
        inspector.append(relations);
        if (selected.uri) { const open = el('a', 'Obsidian에서 원문 열기 ↗'); open.href = selected.uri; inspector.append(open); }
        if (data.scope === 'answer' && selected.citation) {
          const source = el('a', `${selected.citation} 근거 카드로 이동 ↓`); source.href = '#source-' + selected.citation; inspector.append(source);
        }
      }
      onSelect(selected);
    }
    select('');
    return {select};
  }
  return {mount};
})();
