const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const GraphLayout = require('../web/graph-layout.js');

const node = (id, revision = 1) => ({id, kind: 'Note', path: id + '.md', revision, label: id});
const edge = (source, target) => ({source, target, kind: 'linksTo', origin: 'explicit_link'});
const vault = {scope: 'overview', nodes: [node('a', 2), node('b'), node('c')], edges: [edge('a', 'b')]};
const detail = {scope: 'answer', nodes: [node('b'), {id: 'section/b', kind: 'Section', citation: '[1]', path: 'b.md', revision: 1}],
  edges: [{source: 'b', target: 'section/b', kind: 'contains', origin: 'structure'}]};
const result = {id: 'answer1', question: 'b?', graph: detail};
const tick = () => new Promise(resolve => setImmediate(resolve));
function element() {
  return {hidden: false, textContent: '', classList: {toggle() {}}, replaceChildren() {}, scrollIntoView() {}};
}
function harness(get = async () => vault) {
  const elements = new Map(), mounts = [], flights = [], snapshots = [];
  const $ = selector => { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); };
  const context = vm.createContext({$, el: element, api: get, GraphLayout, document: {querySelector: $},
    SourceCards: {highlight() {}}, matchMedia: () => ({matches: false}),
    GraphView: {dispose() {}, mount(container, data) {
      const snapshot = {positions: {a: {x: mounts.length, y: 0, z: 0}}, camera: {x: 1}, target: {x: 0}};
      mounts.push(data); snapshots.push(snapshot);
      return {capture: () => snapshot, select() {}, stopJourney() {},
        journey(ids, options) { flights.push({ids: [...ids], ...options}); }};
    }}
  });
  vm.runInContext(fs.readFileSync('web/chat-graph.js', 'utf8') + '\nglobalThis.graph = ChatGraph;', context);
  return {graph: context.graph, mounts, flights, snapshots, $};
}

test('initial map includes every overview node and restoring an answer does not replace it', async () => {
  const h = harness(); h.graph.sync({vault: '/vault'}); await tick();
  assert.equal(h.mounts.at(-1).scope, 'overview');
  assert.equal(h.mounts.at(-1).nodes.length, 3);
  h.graph.rememberAnswer(result);
  assert.equal(h.mounts.length, 1);
  assert.equal(h.$('#dock-switch').hidden, false);
});

test('job stage updates keep the same scene and visit only newly discovered evidence', async () => {
  const h = harness(); h.graph.sync({vault: '/vault'}); await tick();
  await h.graph.waiting('job1', 'b?');
  h.graph.showJob({id: 'job1', seq: 3, question: 'b?', graph: detail});
  const count = h.mounts.length, searches = h.flights.filter(f => f.phase === 'search').length;
  h.graph.showJob({id: 'job1', seq: 4, question: 'b?', graph: detail});
  assert.equal(h.mounts.length, count);
  assert.equal(h.flights.filter(f => f.phase === 'search').length, searches);
  assert.equal(h.mounts.at(-1).nodes.length, 4);
  assert.equal(h.mounts.at(-1).viewState, h.snapshots.at(-2));
  h.graph.showAnswer(result);
  assert.equal(h.flights.at(-1).phase, 'complete');
  assert.deepEqual(h.flights.at(-1).ids.sort(), ['b', 'section/b']);
});

test('a fast completed answer still tours evidence before fitting every used node', async () => {
  const h = harness(); h.graph.sync({vault: '/vault'}); await tick();
  await h.graph.waiting('fast', 'b?');
  h.graph.showAnswer(result);
  assert.equal(h.flights.at(-1).phase, 'search');
  h.flights.at(-1).onEnd();
  assert.equal(h.flights.at(-1).phase, 'complete');
  assert.deepEqual(h.flights.at(-1).ids.sort(), ['b', 'section/b']);
});

test('late jobs and old journey callbacks cannot interrupt a newer question', async () => {
  const h = harness(); h.graph.sync({vault: '/vault'}); await tick();
  h.graph.showAnswer(result);
  const finish = h.flights.at(-1).onEnd;
  await h.graph.waiting('new', 'c?');
  const count = h.mounts.length, flights = h.flights.length;
  assert.equal(h.graph.showJob({id: 'old', graph: detail}), null);
  finish();
  assert.equal(h.mounts.length, count);
  assert.equal(h.flights.length, flights);
  h.graph.stop();
  assert.equal(h.graph.showJob({id: 'new', graph: detail}), null);
});

test('historical evidence wins its node id without borrowing current-revision edges', async () => {
  const h = harness(); h.graph.sync({vault: '/vault'}); await tick();
  h.graph.showAnswer({id: 'old', question: 'a?', graph: {scope: 'answer', nodes: [node('a', 1)], edges: []}});
  const merged = h.mounts.at(-1);
  assert.equal(merged.nodes.find(n => n.id === 'a').revision, 1);
  assert.equal(merged.nodes.find(n => n.id === 'a').context_only, false);
  assert.equal(merged.nodes.find(n => n.id === 'b').context_only, true);
  assert.equal(merged.edges.length, 0);
});

test('oversized saved evidence and background are bounded before merging while evidence keeps priority', async () => {
  const overview = {scope: 'overview', nodes: Array.from({length: 2000}, (_, i) => node('n' + i)), edges: [], omitted_nodes: 50};
  const h = harness(async () => overview); h.graph.sync({vault: '/vault'}); await tick();
  assert.equal(h.mounts[0].nodes.length, GraphLayout.MAX_NODES);
  assert.equal(h.mounts[0].omitted_nodes, 1810);
  const evidence = {scope: 'answer', nodes: Array.from({length: 600}, (_, i) => ({...node('e' + i), citation: 'S' + i})),
    edges: Array.from({length: 1500}, (_, i) => edge('e' + i % 240, 'e' + (i % 240 + 1 + Math.floor(i / 240)) % 240))};
  h.graph.showAnswer({id: 'saved', question: 'saved?', graph: evidence}, {animate: false});
  const displayed = h.mounts.at(-1);
  assert.equal(displayed.nodes.length, GraphLayout.MAX_NODES);
  assert.equal(displayed.edges.length, GraphLayout.MAX_EDGES);
  assert.ok(displayed.nodes.every(n => n.citation && !n.context_only));
  assert.equal(displayed.background_omitted_nodes, 1810);
  assert.equal(displayed.evidence_omitted_nodes, 360);
  assert.equal(displayed.omitted_nodes, 240);
  assert.ok(displayed.journeyIds.length <= GraphLayout.MAX_NODES);
});

test('a late overview from a previously connected vault never replaces the new map', async () => {
  const resolvers = [];
  const h = harness(() => new Promise(resolve => resolvers.push(resolve)));
  h.graph.sync({vault: '/old'});
  h.graph.sync({vault: '/new'});
  resolvers[1]({...vault, nodes: [node('new')]}); await tick();
  resolvers[0](vault); await tick();
  assert.equal(h.mounts.length, 1);
  assert.equal(h.mounts[0].nodes[0].id, 'new');
});

test('restoring a terminal query keeps the initial overview and does not replay completion', async () => {
  let stopped = 0, completed = 0;
  const nodes = new Map(), values = new Map([['obsi-job', 'finished']]);
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const context = vm.createContext({$, el: element, clearTimeout() {}, setTimeout() {},
    api: async path => path === '/query-jobs' ? [] : {id: 'finished', state: 'completed'},
    document: {querySelector: $, addEventListener() {}},
    sessionStorage: {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)},
    GraphView: {dispose() {}}, ChatGraph: {stop() { stopped++; }}, ChatUI: {complete() { completed++; }}
  });
  vm.runInContext(fs.readFileSync('web/query-jobs.js', 'utf8') + '\nglobalThis.query = QueryUI;', context);
  await context.query.restore();
  assert.equal(stopped, 0);
  assert.equal(completed, 0);
  assert.equal(values.has('obsi-job'), false);
});
