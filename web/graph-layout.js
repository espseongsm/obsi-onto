/* Deterministic spatial layout. Coordinates describe presentation, not semantic distance. */
const GraphLayout = (() => {
  function hashId(id) {
    let hash = 2166136261;
    for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    hash = Math.imul(hash ^ hash >>> 16, 0x85ebca6b);
    return (hash ^ hash >>> 13) >>> 0;
  }
  function positions(nodes, edges) {
    // Sorted API rows must not turn related projects into stacked latitude bands.
    const seeds = nodes.map(node => ({node, hash: hashId(node.id)})).sort((a, b) => a.hash - b.hash || a.node.id.localeCompare(b.node.id));
    const points = new Map(seeds.map(({node}, i) => {
      const y = 1 - 2 * (i + .5) / nodes.length, angle = i * 2.399963;
      const radius = Math.sqrt(1 - y * y) * 130;
      return [node.id, {x: Math.cos(angle) * radius, y: y * 130, z: Math.sin(angle) * radius}];
    }));
    const values = [...points.values()];
    const links = edges.map(edge => [points.get(edge.source), points.get(edge.target), edge.kind === 'contains' ? 46 : 78])
      .filter(([a, b]) => a && b && a !== b);
    const steps = Math.max(24, Math.min(150, Math.floor(24000 / Math.max(1, nodes.length))));
    const stride = Math.max(1, Math.ceil(nodes.length / 240));
    for (let step = 0; step < steps; step++) {
      for (const p of values) { p.dx = -p.x * .008; p.dy = -p.y * .008; p.dz = -p.z * .008; }
      for (let i = 0; i < values.length; i++) for (let j = i + 1 + step % stride; j < values.length; j += stride) {
        const a = values[i], b = values[j];
        const x = a.x - b.x, y = a.y - b.y, z = a.z - b.z, distance = Math.max(1, Math.hypot(x, y, z));
        const force = Math.min(12, 3400 * stride / (distance * distance)) / distance;
        a.dx += x * force; a.dy += y * force; a.dz += z * force;
        b.dx -= x * force; b.dy -= y * force; b.dz -= z * force;
      }
      for (const [a, b, rest] of links) {
        const x = b.x - a.x, y = b.y - a.y, z = b.z - a.z, distance = Math.max(1, Math.hypot(x, y, z));
        const force = (distance - rest) * .025 / distance;
        a.dx += x * force; a.dy += y * force; a.dz += z * force;
        b.dx -= x * force; b.dy -= y * force; b.dz -= z * force;
      }
      const cooling = 1 - step / (steps * 1.2);
      for (const p of values) for (const axis of ['x', 'y', 'z']) p[axis] += Math.max(-12, Math.min(12, p['d' + axis])) * cooling;
    }
    for (const axis of ['x', 'y', 'z']) {
      const center = values.reduce((sum, p) => sum + p[axis], 0) / Math.max(1, values.length);
      for (const p of values) p[axis] -= center;
    }
    const scale = 165 / Math.max(165, ...values.map(p => Math.hypot(p.x, p.y, p.z)));
    return new Map([...points].map(([id, p]) => [id, {x: p.x * scale, y: p.y * scale, z: p.z * scale}]));
  }
  function restore(nodes, edges, saved) {
    const existing = new Map(nodes.filter(node => ['x', 'y', 'z'].every(axis => Number.isFinite(saved?.[node.id]?.[axis])))
      .map(node => [node.id, {...saved[node.id]}]));
    if (!existing.size) return positions(nodes, edges);
    let base;
    const notes = nodes.filter(node => node.kind === 'Note' && existing.has(node.id));
    for (const node of nodes) {
      if (existing.has(node.id)) continue;
      const parent = edges.find(edge => edge.kind === 'contains' && edge.target === node.id && existing.has(edge.source));
      const note = notes.find(item => node.path && item.path === node.path);
      const anchor = existing.get(parent?.source || note?.id);
      if (!anchor) { base ||= positions(nodes, edges); existing.set(node.id, base.get(node.id)); continue; }
      const hash = [...node.id].reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 0);
      const angle = hash * 2.399963, y = (hash % 101) / 50 - 1, radius = 20 + hash % 16;
      const ring = Math.sqrt(Math.max(0, 1 - y * y)) * radius;
      existing.set(node.id, {x: anchor.x + Math.cos(angle) * ring, y: anchor.y + y * radius, z: anchor.z + Math.sin(angle) * ring});
    }
    return existing;
  }
  function focus(nodes, edges, id) {
    const selected = nodes.find(node => node.id === id);
    const seeds = new Set(selected ? [id] : nodes.filter(node => node.matched).map(node => node.id));
    const neighbors = new Set(seeds);
    for (const edge of edges) if (seeds.has(edge.source) || seeds.has(edge.target)) {
      neighbors.add(edge.source); neighbors.add(edge.target);
    }
    return {selected, seeds, neighbors};
  }
  return {positions, restore, focus};
})();
if (typeof module !== 'undefined') module.exports = GraphLayout;
