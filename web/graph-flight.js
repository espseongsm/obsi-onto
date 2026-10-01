/* Camera framing and finite flight paths. Spatial routes are presentation, not reasoning. */
const GraphFlight = (() => {
  const add = (a, b) => ({x: a.x + b.x, y: a.y + b.y, z: a.z + b.z});
  const scale = (a, n) => ({x: a.x * n, y: a.y * n, z: a.z * n});
  const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
  const normalize = a => scale(a, 1 / (Math.hypot(a.x, a.y, a.z) || 1));
  const mix = (a, b, t) => add(scale(a, 1 - t), scale(b, t));
  function frame(points, {width, height, fov = 42, direction = {x: .3, y: .2, z: 1}, minDistance = 100, bottom = width < 420 ? 145 : 110}) {
    const center = {x: 0, y: 0, z: 0};
    for (const axis of ['x', 'y', 'z']) center[axis] = points.length ?
      (Math.min(...points.map(p => p[axis])) + Math.max(...points.map(p => p[axis]))) / 2 : 0;
    const forward = normalize(direction), right = normalize({x: forward.z, y: 0, z: -forward.x});
    const up = {x: forward.y * right.z, y: forward.z * right.x - forward.x * right.z, z: -forward.y * right.x};
    const tangent = Math.tan(fov * Math.PI / 360), aspect = width / Math.max(1, height);
    const top = 50;
    const horizontal = Math.max(.35, (width - 110) / Math.max(1, width));
    const vertical = Math.max(.3, (height - top - bottom) / Math.max(1, height));
    const upper = tangent * (1 - 2 * top / Math.max(1, height)), lower = tangent * (1 - 2 * bottom / Math.max(1, height));
    let distance = minDistance;
    for (const p of points) {
      const relative = add(p, scale(center, -1)), depth = dot(relative, forward), padding = (p.radius || 3) * 2.4;
      distance = Math.max(distance, depth + (Math.abs(dot(relative, right)) + padding) / (tangent * aspect * horizontal),
        (dot(relative, up) + padding + depth * upper) / (tangent * vertical),
        (-dot(relative, up) + padding + depth * lower) / (tangent * vertical));
    }
    distance = Math.min(1800, distance);
    const target = add(center, scale(up, -distance * tangent * (bottom - top) / Math.max(1, height)));
    return {camera: add(target, scale(forward, distance)), target};
  }
  function sample(start, end, progress, arc = .12) {
    const t = Math.max(0, Math.min(1, progress)), ease = t * t * (3 - 2 * t);
    const camera = mix(start.camera, end.camera, ease), target = mix(start.target, end.target, ease);
    const distance = Math.hypot(end.camera.x - start.camera.x, end.camera.y - start.camera.y, end.camera.z - start.camera.z);
    const lift = Math.sin(Math.PI * ease) * distance * arc;
    camera.y += lift; camera.z += lift * .4;
    return {camera, target};
  }
  function stops(ids, coordinates, limit = 3) {
    const known = [...new Set(ids)].filter(id => coordinates.has(id));
    if (known.length <= limit) return known;
    const result = [known[0]];
    while (result.length < limit) {
      let best, gap = -1;
      for (const id of known) {
        if (result.includes(id)) continue;
        const p = coordinates.get(id);
        const nearest = Math.min(...result.map(other => {
          const q = coordinates.get(other); return Math.hypot(p.x - q.x, p.y - q.y, p.z - q.z);
        }));
        if (nearest > gap) { best = id; gap = nearest; }
      }
      result.push(best);
    }
    return result;
  }
  return {frame, sample, stops};
})();
if (typeof module !== 'undefined') module.exports = GraphFlight;
