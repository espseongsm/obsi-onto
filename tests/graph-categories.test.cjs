const test = require('node:test');
const assert = require('node:assert/strict');
const {decorate, items} = require('../web/graph-categories.js');
const note = (id, label, path = label + '.md') => ({id, label, path, kind: 'Note'});

test('knowledge areas distinguish subjects instead of assigning every note the same color', () => {
  const labels = ['Claude models', 'Snowflake warehouse', 'Docker SSH', 'Project roadmap', 'QQQ portfolio',
    'Inflation and tax', 'Family exercise', 'Obsidian ontology', '2026-09-30', 'Unlabeled'];
  const result = decorate({nodes: labels.map((label, i) => note(String(i), label)), edges: []});
  assert.deepEqual(result.nodes.map(n => n.category), items.map(item => item.id));
  assert.ok(result.nodes.every(n => n.categoryReason));
});

test('explicit metadata takes priority, metadata reaches source notes, and neutral passages inherit', () => {
  const nodes = [note('n', 'AI meeting'), {id: 's', kind: 'Section', label: 'Summary', path: 'AI meeting.md'},
    {id: 't', kind: 'Tag', label: '투자'}];
  const edges = [{source: 'n', target: 's', kind: 'contains'}, {source: 's', target: 't', kind: 'taggedWith'}];
  const result = decorate({nodes, edges});
  assert.equal(result.nodes[0].category, 'investing');
  assert.equal(result.nodes[1].category, 'investing');
  assert.match(result.nodes[0].categoryReason, /tag.*투자/);
  const inherited = decorate({nodes: [note('n', 'Docker setup'), {id: 's', kind: 'Section', label: 'Summary', path: 'Docker setup.md'}],
    edges: [{source: 'n', target: 's', kind: 'contains'}]});
  assert.equal(inherited.nodes[1].category, 'engineering');
  assert.match(inherited.nodes[1].categoryReason, /source note/);
});

test('English word boundaries and decomposed Korean names are handled without scanning source text', () => {
  const nodes = [note('a', 'Daily entry'), note('b', '백테스트 노트'.normalize('NFD')),
    {...note('c', 'Unlabeled'), excerpt: 'stock market AI tax data'}, note('d', 'Overview', 'Investing/Overview.md')];
  const result = decorate({nodes, edges: []});
  assert.deepEqual(result.nodes.map(n => n.category), ['journal', 'investing', 'other', 'investing']);
  // The folder itself is a useful signal when it uses a recognized area name.
  assert.equal(decorate({nodes: [note('d', 'Overview', 'Stock research/Overview.md')], edges: []}).nodes[0].category, 'investing');
});

test('classification is order independent and never mutates source snapshots or relationships', () => {
  const data = {scope: 'answer', nodes: [note('n', 'Record'), {id: 'a', kind: 'Tag', label: 'AI'}, {id: 'b', kind: 'Topic', label: 'Data'}],
    edges: [{source: 'n', target: 'a', kind: 'taggedWith'}, {source: 'n', target: 'b', kind: 'about'}]};
  const before = JSON.stringify(data), result = decorate(data);
  const reversed = decorate({...data, nodes: [...data.nodes].reverse(), edges: [...data.edges].reverse()});
  assert.equal(result.nodes[0].category, reversed.nodes.at(-1).category);
  assert.equal(result.nodes[0].categoryReason, reversed.nodes.at(-1).categoryReason);
  assert.equal(JSON.stringify(data), before);
  assert.equal(result.edges, data.edges);
  assert.equal(result.nodes[0].label, data.nodes[0].label);
});
