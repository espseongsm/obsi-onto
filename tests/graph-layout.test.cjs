const test = require('node:test');
const assert = require('node:assert/strict');
const {positions, focus} = require('../web/graph-layout.js');

test('empty and isolated records have usable coordinates', () => {
  assert.equal(positions([], []).size, 0);
  assert.deepEqual(positions([{id: 'alone'}], []).get('alone'), {x: 0, y: 0, z: 0});
  assert.equal(focus([], [], '').seeds.size, 0);
});

test('maximum graph stays bounded, deterministic and three dimensional without changing evidence', () => {
  const nodes = Array.from({length: 80}, (_, i) => ({id: String(i), label: '기록 ' + i}));
  const edges = Array.from({length: 200}, (_, i) => ({source: String(i % 80), target: String((i * 7 + 1) % 80), kind: 'linksTo'}));
  const original = JSON.stringify({nodes, edges});
  const points = positions(nodes, edges);
  assert.deepEqual(points, positions(nodes, edges));
  assert.equal(JSON.stringify({nodes, edges}), original);
  assert.equal(points.size, nodes.length);
  for (const p of points.values()) {
    assert.ok(Object.values(p).every(Number.isFinite));
    assert.ok(Math.hypot(p.x, p.y, p.z) <= 165.000001);
  }
  for (const axis of ['x', 'y', 'z']) {
    const values = [...points.values()].map(p => p[axis]);
    assert.ok(Math.max(...values) - Math.min(...values) > 50);
    assert.ok(Math.abs(values.reduce((a, b) => a + b, 0)) < .000001);
  }
});

test('dangling and self links do not corrupt layout', () => {
  const nodes = [{id: 'a'}, {id: 'b'}];
  const edges = [{source: 'a', target: 'missing'}, {source: 'a', target: 'a'}];
  assert.deepEqual(positions(nodes, edges), positions(nodes, []));
});

test('search focus highlights matches and their direct neighbors only', () => {
  const nodes = [{id: 'a', matched: true}, {id: 'b'}, {id: 'c'}, {id: 'd', matched: true}];
  const edges = [{source: 'a', target: 'b'}, {source: 'b', target: 'c'}];
  const result = focus(nodes, edges, '');
  assert.deepEqual(result.seeds, new Set(['a', 'd']));
  assert.deepEqual(result.neighbors, new Set(['a', 'd', 'b']));
  assert.equal(result.selected, undefined);
});

test('selection overrides search focus and clearing restores it', () => {
  const nodes = [{id: 'a', matched: true}, {id: 'b'}, {id: 'c'}];
  const edges = [{source: 'a', target: 'b'}, {source: 'b', target: 'c'}];
  assert.deepEqual(focus(nodes, edges, 'c').neighbors, new Set(['c', 'b']));
  assert.equal(focus(nodes, edges, 'c').selected, nodes[2]);
  assert.deepEqual(focus(nodes, edges, '').seeds, new Set(['a']));
  assert.deepEqual(focus(nodes, edges, 'absent').seeds, new Set(['a']));
});

const {restore} = require('../web/graph-layout.js');
const {frame, sample, stops} = require('../web/graph-flight.js');
const {PerspectiveCamera, Vector3} = require('three');

test('adding answer sections preserves every overview coordinate and anchors new evidence near its note', () => {
  const nodes = [{id: 'note', kind: 'Note', path: 'a.md'}, {id: 'other', kind: 'Note', path: 'b.md'}];
  const saved = Object.fromEntries(positions(nodes, [])), original = structuredClone(saved);
  const next = [...nodes, {id: 'section', kind: 'Section', path: 'a.md'}, {id: 'path-section', kind: 'Section', path: 'b.md'}];
  const edges = [{source: 'note', target: 'section', kind: 'contains'}];
  const result = restore(next, edges, saved);
  assert.deepEqual(result.get('note'), saved.note);
  assert.deepEqual(result.get('other'), saved.other);
  for (const [section, note] of [['section', 'note'], ['path-section', 'other']]) {
    const p = result.get(section), anchor = result.get(note);
    const distance = Math.hypot(p.x - anchor.x, p.y - anchor.y, p.z - anchor.z);
    assert.ok(distance >= 19.999 && distance <= 35.001);
  }
  assert.deepEqual(saved, original);
  assert.deepEqual(result, restore(next, edges, saved));
});

test('missing or invalid snapshot falls back to usable deterministic coordinates', () => {
  const nodes = [{id: 'a'}, {id: 'b'}];
  assert.deepEqual(restore(nodes, [], {a: {x: NaN, y: 0, z: 0}}), positions(nodes, []));
});

test('camera fit includes depth and keeps every target inside narrow and wide viewports', () => {
  const points = Array.from({length: 90}, (_, i) => ({x: Math.sin(i * 2.4) * 160, y: Math.cos(i * 1.8) * 160,
    z: Math.sin(i * .9) * 160, radius: 5}));
  for (const [width, height] of [[340, 560], [700, 900], [1100, 560]]) {
    const bottom = width < 420 ? 185 : 150;
    const view = frame(points, {width, height, bottom});
    const camera = new PerspectiveCamera(42, width / height, .1, 2800);
    camera.position.set(view.camera.x, view.camera.y, view.camera.z);
    camera.lookAt(new Vector3(view.target.x, view.target.y, view.target.z)); camera.updateMatrixWorld();
    for (const p of points) {
      const projection = new Vector3(p.x, p.y, p.z).project(camera);
      const x = (projection.x + 1) * width / 2, y = (1 - projection.y) * height / 2;
      assert.ok(x >= 55 && x <= width - 55, `horizontal ${width}×${height}: ${x}`);
      assert.ok(y >= 50 && y <= height - bottom, `vertical ${width}×${height}: ${y}`);
      assert.ok(projection.z > -1 && projection.z < 1);
    }
  }
});

test('flight is finite with exact endpoints and only visits known evidence ids', () => {
  const start = {camera: {x: 0, y: 0, z: 600}, target: {x: 0, y: 0, z: 0}};
  const end = {camera: {x: 300, y: 50, z: 200}, target: {x: 100, y: 40, z: 30}};
  assert.deepEqual(sample(start, end, 0), start);
  const arrived = sample(start, end, 1);
  for (const key of ['camera', 'target']) for (const axis of ['x', 'y', 'z']) {
    assert.ok(Math.abs(arrived[key][axis] - end[key][axis]) < 1e-10);
  }
  const middle = sample(start, end, .5);
  assert.ok(middle.camera.y > 25);
  const nodes = Array.from({length: 12}, (_, i) => ({id: String(i)})), map = positions(nodes, []);
  const route = stops(['missing', ...nodes.map(node => node.id), '0'], map);
  assert.equal(route.length, 3); assert.equal(new Set(route).size, 3);
  assert.ok(route.every(id => map.has(id))); assert.equal(route[0], '0');
  assert.deepEqual(stops([], map), []);
});

test('project-sorted overview records form a three dimensional map independent of API row order', () => {
  const nodes = [], edges = [];
  for (let group = 0; group < 3; group++) for (let i = 0; i < 12; i++) {
    const id = `note/${group}/${i}`; nodes.push({id, kind: 'Note'});
    for (const [target, kind] of [[`note/${group}/${(i + 1) % 12}`, 'linksTo'], [`tag/${group}`, 'taggedWith'], [`topic/${group}`, 'about']]) {
      edges.push({source: id, target, kind});
    }
  }
  for (let group = 0; group < 3; group++) nodes.push({id: `tag/${group}`, kind: 'Tag'}, {id: `topic/${group}`, kind: 'Topic'});
  const result = positions(nodes, edges), values = [...result.values()];
  const ranges = ['x', 'y', 'z'].map(axis => Math.max(...values.map(p => p[axis])) - Math.min(...values.map(p => p[axis])));
  assert.ok(Math.min(...ranges) > 150);
  assert.ok(Math.max(...ranges) / Math.min(...ranges) < 2);
  assert.deepEqual(result, positions([...nodes].reverse(), edges));
});
