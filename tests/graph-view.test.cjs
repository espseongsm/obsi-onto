const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const GraphLayout = require('../web/graph-layout.js');

class Element {
  constructor(tag = 'div', text = '') {
    this.tag = tag; this.textContent = text; this.children = []; this.style = {}; this.attributes = {};
    this.listeners = new Map(); this.classList = {add() {}, remove() {}, toggle() {}};
    this.offsetHeight = 480; this.hidden = false;
  }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  contains(child) { return child === this || this.children.some(node => node.contains(child)); }
  closest(selector) { return selector === this.tag ? this : this.parent?.closest(selector) || null; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  removeEventListener(name) { this.listeners.delete(name); }
  toggle(open) { this.open = open; this.listeners.get('toggle')?.(); }
}
function harness(decorate = data => ({...data, nodes: data.nodes.map(node =>
  ({...node, category: 'other', categoryReason: 'No category rule matched.'}))})) {
  const observers = [], mounts = [], selections = [];
  let active = 0;
  class Observer {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
    notify(near) { this.callback([{isIntersecting: near}]); }
  }
  const context = vm.createContext({GraphLayout, uiText: text => text,
    GraphCategories: {decorate, items: [{id: 'ai', label: 'AI & ML'}, {id: 'work', label: 'Work'}, {id: 'other', label: 'Other'}]},
    el: (tag, text, className = '') => Object.assign(new Element(tag, text), {className}),
    Option: class extends Element { constructor(text, value) { super('option', text); this.value = value; } },
    IntersectionObserver: Observer, document: {hidden: false}, window: {addEventListener() {}},
    GraphScene: {mount(stage, data) {
      active++; mounts.push(data);
      const snapshot = {positions: {a: {x: 10, y: 20, z: 30}}, camera: {x: 40, y: 50, z: 60}, target: {x: 0, y: 0, z: 0}};
      return {capture: () => snapshot, select: focus => selections.push(focus.selected?.id || ''),
        visible() {}, dispose() { active--; }};
    }}
  });
  vm.runInContext(fs.readFileSync('web/graph.js', 'utf8') + '\nglobalThis.view = GraphView;', context);
  return {view: context.view, observers, mounts, selections, active: () => active};
}
const data = {nodes: [{id: 'a', kind: 'Note', label: 'Original note'}], edges: []};
function canvas(open = true) {
  const disclosure = new Element('details'), container = new Element();
  disclosure.open = open; disclosure.append(container); return {disclosure, container};
}

test('many open answer graphs mount only near the viewport and restore their captured camera', () => {
  const h = harness(), items = Array.from({length: 50}, () => canvas());
  const watchers = items.map(({container}) => h.view.watch(container, data));
  assert.equal(h.active(), 0);
  watchers[0].select('a'); h.observers[0].notify(true);
  assert.equal(h.active(), 1); assert.equal(h.selections.at(-1), 'a');
  h.observers[1].notify(true); assert.equal(h.active(), 2);
  h.observers[0].notify(false); assert.equal(h.active(), 1);
  assert.equal(items[0].container.style.minHeight, '480px');
  assert.equal(h.observers[0].disconnected, false);
  h.observers[0].notify(true);
  assert.equal(h.active(), 2);
  assert.equal(h.mounts.at(-1).viewState.camera.z, 60);
  assert.equal(h.selections.at(-1), 'a');
});

test('closing a disclosure releases its scene, and root disposal disconnects its watcher', () => {
  const h = harness(), {disclosure, container} = canvas(false);
  h.view.watch(container, data); h.observers[0].notify(true);
  assert.equal(h.active(), 0);
  disclosure.toggle(true); assert.equal(h.active(), 1);
  disclosure.toggle(false); assert.equal(h.active(), 0);
  disclosure.toggle(true); assert.equal(h.active(), 1);
  h.view.dispose(disclosure);
  assert.equal(h.active(), 0); assert.equal(h.observers[0].disconnected, true);
  assert.equal(disclosure.listeners.has('toggle'), false);
  h.observers[0].notify(true); disclosure.toggle(true);
  assert.equal(h.active(), 0);
});


test('knowledge-area colors group mixed node types and expose classification without rewriting source data', () => {
  const original = {nodes: [
    {id: 'note', kind: 'Note', label: '원래 노트 제목'},
    {id: 'passage', kind: 'Section', label: 'Original passage'},
    {id: 'budget', kind: 'Note', label: 'Budget'}
  ], edges: [{source: 'note', target: 'passage', kind: 'contains', origin: 'structure'}]};
  const before = JSON.stringify(original);
  const h = harness(data => ({...data, nodes: data.nodes.map(node => ({...node,
    category: node.id === 'budget' ? 'work' : 'ai',
    categoryReason: node.id === 'budget' ? 'Matched folder: Work' : 'Matched tag: AI'}))}));
  const {container} = canvas();
  const view = h.view.mount(container, original);
  const descendants = node => [node, ...node.children.flatMap(descendants)];
  const hasClass = (node, value) => node.className?.split(' ').includes(value);
  const legend = descendants(container).find(node => hasClass(node, 'graph-legend'));
  assert.equal(legend.getAttribute('aria-label'), 'Knowledge area color legend');
  assert.deepEqual(legend.children.filter(node => hasClass(node, 'graph-legend-item'))
    .map(node => [node.className, node.textContent]),
  [['graph-legend-item category-ai', 'AI & ML 2'], ['graph-legend-item category-work', 'Work 1']]);
  assert.equal(h.mounts[0].nodes[1].category, 'ai');
  assert.equal(h.mounts[0].edges, original.edges);
  const picker = descendants(container).find(node => node.tag === 'select');
  assert.equal(picker.children[1].textContent, 'Note · 원래 노트 제목');
  assert.equal(picker.children[2].textContent, 'Passage · Original passage');
  view.select('note');
  const inspector = descendants(container).find(node => hasClass(node, 'graph-inspector'));
  assert.equal(inspector.children.find(node => hasClass(node, 'graph-kind-badge')).textContent, 'AI & ML');
  assert.equal(inspector.children.find(node => hasClass(node, 'graph-node-kind')).textContent, 'Note');
  assert.equal(inspector.children.find(node => hasClass(node, 'graph-category-reason')).textContent, 'Matched tag: AI');
  assert.equal(inspector.children.find(node => node.tag === 'h4').textContent, '원래 노트 제목');
  assert.equal(JSON.stringify(original), before);
});
