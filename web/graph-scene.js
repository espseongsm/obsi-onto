/* A constellation of real records. Animation settles; idle views do not render. */
const GraphScene = (() => {
  const defaultColors = {Note: '#438dff', Section: '#00d7ed', Topic: '#ffd949', Tag: '#bd69ff', Claim: '#ff659e', Activity: '#ff963d'};
  const defaultCategories = {ai: '#b38aff', data: '#13d9ed', engineering: '#559cff', work: '#ffa64c', investing: '#f5d94f',
    economy: '#ff7485', life: '#6cdaa2', knowledge: '#eb87df', journal: '#dcaf82', other: '#a6b4ca'};
  function mount(stage, data, onSelect) {
    const T = ObsiThree, reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    function readPalette() {
      const style = getComputedStyle(stage), value = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
      return {
        light: document.documentElement.dataset.theme === 'light',
        nodes: Object.fromEntries(Object.entries(defaultColors).map(([kind, fallback]) => [kind, value('--node-' + kind.toLowerCase(), fallback)])),
        categories: Object.fromEntries(Object.entries(defaultCategories).map(([category, fallback]) => [category, value('--category-' + category, fallback)])),
        edge: value('--graph-edge', '#597fb7'), strong: value('--graph-edge-strong', '#91bbf4'),
        focus: value('--graph-focus-color', '#ffd39a'), match: value('--graph-match-color', '#96d1ff'),
        conflict: value('--graph-conflict-color', '#efb878'), background: value('--graph-bg', '#071528')
      };
    }
    function nodeColor(node) { return palette.categories[node.category] || palette.nodes[node.kind] || palette.nodes.Section; }
    let palette = readPalette(), currentFocus = {seeds: new Set(), neighbors: new Set()};
    const renderer = new T.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    const canvas = renderer.domElement;
    canvas.setAttribute('aria-label', '3D knowledge graph. Nodes in the same knowledge category share a color. Drag or use arrow keys to rotate, and scroll to zoom. You can also use the node selection list.');
    canvas.tabIndex = 0; stage.prepend(canvas); stage.dataset.renderer = '3d';
    const labelLayer = el('div', undefined, 'graph-labels'); stage.append(labelLayer);
    const scene = new T.Scene(), camera = new T.PerspectiveCamera(42, 1, .1, 2800);
    scene.fog = new T.FogExp2(palette.background, .0003);
    const ambient = new T.AmbientLight('#ffffff', 1.1), key = new T.DirectionalLight('#ffffff', 2.5);
    const rim = new T.DirectionalLight('#93baff', 1.4);
    key.position.set(-180, 250, 300); rim.position.set(180, -80, -220); scene.add(ambient, key, rim);
    const controls = new T.OrbitControls(camera, canvas);
    controls.enableDamping = false; controls.minDistance = 65; controls.maxDistance = 1800;
    controls.minPolarAngle = .12; controls.maxPolarAngle = Math.PI - .12; controls.autoRotateSpeed = .32;
    const glowCanvas = document.createElement('canvas'); glowCanvas.width = glowCanvas.height = 128;
    const context = glowCanvas.getContext('2d'), gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
    gradient.addColorStop(0, '#ffffffff'); gradient.addColorStop(.08, '#ffffffee');
    gradient.addColorStop(.22, '#ffffff66'); gradient.addColorStop(.5, '#ffffff19'); gradient.addColorStop(1, '#ffffff00');
    context.fillStyle = gradient; context.fillRect(0, 0, 128, 128);
    const glowMap = new T.CanvasTexture(glowCanvas), sphere = new T.SphereGeometry(1, 20, 12);
    const ring = new T.RingGeometry(1.62, 1.68, 64), diamond = new T.RingGeometry(1.5, 1.65, 4);
    const arrowShape = new T.ConeGeometry(.75, 2.9, 6);
    const coordinates = GraphLayout.restore(data.nodes, data.edges, data.viewState?.positions);
    const entries = new Map(), lines = [], pickable = [], degrees = new Map();
    for (const edge of data.edges) {
      degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1);
      degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1);
    }
    function glow(color, size, opacity) {
      const sprite = new T.Sprite(new T.SpriteMaterial({map: glowMap, color, transparent: true, opacity, depthWrite: false,
        blending: palette.light ? T.NormalBlending : T.AdditiveBlending}));
      sprite.scale.setScalar(size); return sprite;
    }
    for (const node of data.nodes) {
      const p = coordinates.get(node.id), color = nodeColor(node), degree = degrees.get(node.id) || 0;
      const radius = ((node.kind === 'Note' ? 5.4 : node.kind === 'Section' ? 3.1 : 4.5) + Math.min(1.25, Math.sqrt(degree) * .28)) * .85;
      const mesh = new T.Mesh(sphere, new T.MeshStandardMaterial({color, roughness: .36, metalness: .12, emissive: color, emissiveIntensity: .16, transparent: true}));
      mesh.position.set(p.x, p.y, p.z); mesh.scale.setScalar(radius); mesh.userData.id = node.id;
      const halo = glow(color, radius * 6, .15); halo.position.copy(mesh.position);
      const outline = new T.Mesh(node.kind === 'Tag' ? diamond : ring, new T.MeshBasicMaterial({color, transparent: true, opacity: .3, depthWrite: false}));
      outline.position.copy(mesh.position); outline.scale.setScalar(radius);
      const label = el('button', node.label.length > 25 ? node.label.slice(0, 24) + '…' : node.label, 'graph-label kind-' + node.kind + (node.category ? ' category-' + node.category : ''));
      label.type = 'button'; label.title = node.label; label.onclick = () => onSelect(node.id);
      label.onpointerdown = event => { event.preventDefault(); label.focus({preventScroll: true}); };
      label.setAttribute('aria-label', 'Select: ' + node.label); labelLayer.append(label);
      entries.set(node.id, {node, mesh, halo, outline, label, radius, degree, dim: false, opacity: 1, haloOpacity: .15});
      pickable.push(mesh); scene.add(mesh, halo, outline);
    }
    // A slight bow separates overlapping routes while keeping both endpoints exact.
    function route(a, b, index) {
      const delta = b.clone().sub(a), middle = a.clone().add(b).multiplyScalar(.5);
      const normal = new T.Vector3(-delta.y, delta.x, delta.z * .1).normalize();
      middle.addScaledVector(normal, Math.min(21, delta.length() * .13) * (index % 2 ? 1 : -1));
      const points = [];
      for (let i = 0; i <= 40; i++) {
        const t = i / 40;
        points.push(a.clone().multiplyScalar((1 - t) ** 2).addScaledVector(middle, 2 * (1 - t) * t).addScaledVector(b, t * t));
      }
      return points;
    }
    for (const [index, edge] of data.edges.entries()) {
      const a = entries.get(edge.source)?.mesh.position, b = entries.get(edge.target)?.mesh.position;
      if (!a || !b) continue;
      const points = route(a, b, index), conflict = edge.kind === 'conflict_candidate';
      const material = conflict ? new T.LineDashedMaterial({color: palette.conflict, dashSize: 3, gapSize: 3, transparent: true, opacity: .6, depthWrite: false}) :
        new T.LineBasicMaterial({color: palette.edge, transparent: true, opacity: .24, depthWrite: false});
      const line = new T.Line(new T.BufferGeometry().setFromPoints(points), material);
      if (conflict) line.computeLineDistances();
      const arrow = new T.Mesh(arrowShape, new T.MeshBasicMaterial({color: palette.focus, transparent: true, depthWrite: false}));
      arrow.position.copy(points[29]);
      arrow.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), points[30].clone().sub(points[28]).normalize());
      arrow.visible = false;
      const beacon = glow(palette.focus, 9, 0); beacon.visible = false;
      scene.add(line, arrow, beacon); lines.push({edge, line, arrow, beacon, points, index, opacity: conflict ? .6 : .24, emphasized: false});
    }
    const signal = new T.Mesh(ring, new T.MeshBasicMaterial({color: palette.focus, transparent: true, opacity: 0, depthWrite: false}));
    signal.visible = false; scene.add(signal);
    let selected = '', hover = '', labels = true, visible = true, inView = true;
    let disposed = false, frame = 0, resizeFit = 0, lastTime = 0, pointer, fitted = false, userCamera = false, width = 0, height = 0;
    let born = performance.now() - (data.viewState ? 1100 : 0), transition = 0, transitionEnd = 0, focusTravel = null, overview = null, interacting = false;
    let flight = null, pendingJourney = null, journeyIds = new Set(), pausedAt = 0;
    const raycaster = new T.Raycaster(), cursor = new T.Vector2(), projected = new T.Vector3();
    const clamp = value => Math.max(0, Math.min(1, value));
    function requestRender() {
      if (!disposed && visible && inView && !document.hidden && !frame) frame = requestAnimationFrame(render);
    }
    function priority(entry) {
      return entry.node.id === canvas.dataset.activeNode ? 1100 : entry.node.id === selected ? 1000 : entry.node.id === hover ? 900 : entry.node.matched ? 800 :
        entry.dim ? 0 : (entry.node.kind === 'Note' ? 400 : 100) + entry.degree;
    }
    function render(time, resized = false) {
      frame = 0;
      if (disposed || !visible || !inView || document.hidden || !stage.isConnected || !width || !height) return;
      if (!resized && controls.autoRotate && time - lastTime < 32) { requestRender(); return; }
      if (focusTravel) {
        const travel = focusTravel, progress = reducedMotion.matches ? 1 : clamp((time - travel.start) / travel.duration);
        const next = GraphFlight.sample(travel.from, travel.to, progress, travel.arc);
        camera.position.set(next.camera.x, next.camera.y, next.camera.z);
        controls.target.set(next.target.x, next.target.y, next.target.z);
        if (progress === 1) { focusTravel = null; travel.onEnd?.(); }
      }
      controls.update(controls.autoRotate ? Math.min(.05, (time - lastTime) / 1000) : 0); lastTime = time;
      const reveal = reducedMotion.matches ? 1 : clamp((time - born) / 1050), ease = 1 - (1 - reveal) ** 3;
      const passage = reducedMotion.matches || !transition ? 1 : clamp((time - transition) / 900);
      for (const entry of entries.values()) {
        const active = entry.node.id === selected || entry.node.id === hover || entry.node.id === canvas.dataset.activeNode;
        entry.mesh.scale.setScalar(entry.radius * (.78 + ease * .22) * (active ? 1.14 : 1));
        entry.mesh.material.opacity = entry.opacity * ease;
        entry.halo.material.opacity = entry.haloOpacity * ease;
        entry.outline.quaternion.copy(camera.quaternion);
        entry.outline.material.opacity = (entry.dim ? .06 : active ? .85 : entry.node.matched ? .58 : .23) * ease;
      }
      for (const item of lines) {
        item.line.material.opacity = item.opacity * ease;
        item.line.geometry.setDrawRange(0, Math.max(2, Math.ceil(41 * ease)));
        item.beacon.visible = item.emphasized && !reducedMotion.matches && passage < 1;
        if (item.beacon.visible) {
          const progress = passage * 40, index = Math.min(39, Math.floor(progress));
          item.beacon.position.copy(item.points[index]).lerp(item.points[index + 1], progress - index);
          item.beacon.material.opacity = Math.sin(passage * Math.PI) * .8;
        }
      }
      const picked = entries.get(selected || canvas.dataset.activeNode);
      signal.visible = !!picked && passage < 1 && !reducedMotion.matches;
      if (signal.visible) {
        signal.position.copy(picked.mesh.position); signal.quaternion.copy(camera.quaternion);
        signal.scale.setScalar(picked.radius * (1.1 + passage * 3.6)); signal.material.opacity = (1 - passage) * .55;
      }
      renderer.render(scene, camera);
      const occupied = [], bottom = (width < 420 ? 145 : 110) + (data.journeyIds?.length ? 40 : 0), budget = Math.min(20, Math.max(6, Math.floor(width * height / 24000)));
      for (const entry of [...entries.values()].sort((a, b) => priority(b) - priority(a))) {
        const important = entry.node.id === selected || entry.node.id === hover || entry.node.id === canvas.dataset.activeNode;
        projected.copy(entry.mesh.position).project(camera);
        const x = (projected.x + 1) * width / 2, y = (1 - projected.y) * height / 2 + (entry.node.kind === 'Note' ? 18 : 13);
        const labelWidth = Math.min(184, entry.label.textContent.length * 8.6 + 30);
        const labelX = Math.max(labelWidth / 2 + 8, Math.min(width - labelWidth / 2 - 8, x));
        const box = {x: labelX - labelWidth / 2, y, w: labelWidth, h: 30};
        const collision = occupied.some(other => box.x < other.x + other.w && box.x + box.w > other.x && box.y < other.y + other.h && box.y + box.h > other.y);
        const shown = projected.z > -1 && projected.z < 1 && x > 20 && x < width - 20 && y > 50 && y < height - bottom &&
          (important || labels && !entry.dim && !collision && occupied.length < budget);
        entry.label.hidden = !shown;
        if (shown) {
          entry.label.style.transform = `translate(${labelX}px,${y}px) translateX(-50%)`;
          entry.label.style.zIndex = important ? '3' : '1'; entry.label.style.opacity = String(ease); occupied.push(box);
        }
      }
      canvas.dataset.camera = camera.position.toArray().map(value => value.toFixed(1)).join(',');
      canvas.dataset.target = controls.target.toArray().map(value => value.toFixed(1)).join(',');
      if (controls.autoRotate || reveal < 1 || time < transitionEnd || focusTravel) requestRender();
    }
    function cameraState() {
      return {camera: {x: camera.position.x, y: camera.position.y, z: camera.position.z},
        target: {x: controls.target.x, y: controls.target.y, z: controls.target.z}};
    }
    function framing(ids, direction) {
      return GraphFlight.frame(ids.map(id => entries.get(id)).filter(Boolean).map(({mesh, radius}) => ({...mesh.position, radius})),
        {width, height, fov: camera.fov, direction, bottom: (width < 420 ? 145 : 110) + (data.journeyIds?.length ? 40 : 0)});
    }
    function applyCamera(view) {
      camera.position.set(view.camera.x, view.camera.y, view.camera.z);
      controls.target.set(view.target.x, view.target.y, view.target.z); controls.update(); requestRender();
    }
    function stopJourney() {
      pendingJourney = null; flight = null; focusTravel = null; userCamera = true; delete canvas.dataset.activeNode;
      if (canvas.dataset.journey === 'running') canvas.dataset.journey = 'stopped';
      requestRender();
    }
    function reset() {
      if (!width || !height) return;
      stopJourney(); overview = null; userCamera = false;
      applyCamera(framing([...entries.keys()]));
    }
    function journey(ids, options = {}) {
      const known = [...new Set(ids)].filter(id => entries.has(id));
      if (!fitted) { pendingJourney = {ids: known, options}; return; }
      stopJourney(); controls.autoRotate = false; overview = null; userCamera = false;
      const phase = options.phase || 'search';
      journeyIds = phase === 'overview' ? new Set() : new Set(known);
      select({...currentFocus, selected: undefined});
      canvas.dataset.journey = 'running'; canvas.dataset.phase = phase; canvas.dataset.targetIds = JSON.stringify(known);
      const stops = phase === 'search' && !reducedMotion.matches ? GraphFlight.stops(known, coordinates) : [];
      flight = {ids: known, phase, stops, index: 0, onEnd: options.onEnd};
      function advance() {
        if (!flight) return;
        const active = flight;
        if (active.index < active.stops.length) {
          const id = active.stops[active.index++], nearby = [id];
          for (const item of lines) {
            if (item.edge.source === id && journeyIds.has(item.edge.target)) nearby.push(item.edge.target);
            if (item.edge.target === id && journeyIds.has(item.edge.source)) nearby.push(item.edge.source);
          }
          canvas.dataset.step = active.index + '/' + (active.stops.length + 1);
          canvas.dataset.activeNode = id;
          const direction = {x: .3 + Math.sin(active.index * .85) * .45, y: .18, z: 1};
          const anchor = entries.get(id).mesh.position;
          const local = nearby.filter(other => entries.get(other).mesh.position.distanceTo(anchor) < 75)
            .sort((a, b) => entries.get(a).mesh.position.distanceTo(anchor) - entries.get(b).mesh.position.distanceTo(anchor));
          const view = framing(local.slice(0, 4), direction);
          transition = performance.now(); transitionEnd = transition + 1200;
          travelTo(view.camera, view.target, 1350, advance, .2);
        } else {
          canvas.dataset.step = (active.stops.length + 1) + '/' + (active.stops.length + 1);
          delete canvas.dataset.activeNode;
          const view = framing(active.ids.length ? active.ids : [...entries.keys()]);
          travelTo(view.camera, view.target, active.phase === 'overview' ? 1100 : 1200, () => {
            if (flight !== active) return;
            flight = null; canvas.dataset.journey = 'complete'; active.onEnd?.();
          });
        }
      }
      advance(); requestRender();
    }
    const resize = new ResizeObserver(() => {
      clearTimeout(resizeFit); resizeFit = 0;
      width = stage.clientWidth; height = stage.clientHeight;
      if (!width || !height) return;
      renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
      if (!fitted) {
        fitted = true;
        if (data.viewState?.camera && data.viewState?.target) applyCamera(data.viewState);
        else {
          const end = framing([...entries.keys()]);
          applyCamera(framing([...entries.keys()], {x: -.08, y: .25, z: 1}));
          travelTo(end.camera, end.target, 1500);
        }
        if (pendingJourney) { const pending = pendingJourney; pendingJourney = null; journey(pending.ids, pending.options); }
        else if (selected) focusCamera(selected);
      }
      else if (!userCamera && !flight) {
        // Reframe after pane resizing settles; world coordinates stay fixed.
        resizeFit = setTimeout(() => {
          resizeFit = 0;
          if (disposed || userCamera || flight || interacting || !visible || document.hidden || !stage.isConnected || !stage.clientWidth || !stage.clientHeight) return;
          applyCamera(framing(journeyIds.size ? [...journeyIds] : [...entries.keys()])); if (selected) focusCamera(selected);
        }, 150);
      }
      // Resizing clears the drawing buffer; repaint during the same resize cycle.
      cancelAnimationFrame(frame); frame = 0; render(performance.now(), true);
    }); resize.observe(stage);
    const intersection = new IntersectionObserver(items => {
      inView = items[0].isIntersecting;
      if (inView) resumeMotion(); else pauseMotion();
    }); intersection.observe(stage);
    controls.addEventListener('change', () => { if (interacting) { overview = null; userCamera = true; } requestRender(); });
    controls.addEventListener('start', () => { interacting = true; userCamera = true; stopJourney(); });
    controls.addEventListener('end', () => { interacting = false; });
    function pauseMotion() { pausedAt ||= performance.now(); cancelAnimationFrame(frame); frame = 0; }
    function resumeMotion() {
      if (!visible || !inView || document.hidden) return;
      if (pausedAt) {
        const elapsed = performance.now() - pausedAt;
        born += elapsed; transition += elapsed; transitionEnd += elapsed;
        if (focusTravel) focusTravel.start += elapsed;
        pausedAt = 0;
      }
      requestRender();
    }
    function visibilityChanged() { if (document.hidden) pauseMotion(); else resumeMotion(); }
    document.addEventListener('visibilitychange', visibilityChanged);
    function motionChanged() {
      if (reducedMotion.matches) {
        controls.autoRotate = false; transitionEnd = 0;
        if (flight) journey(flight.ids, {phase: flight.phase, onEnd: flight.onEnd});
      }
      requestRender();
    }
    reducedMotion.addEventListener('change', motionChanged);
    function hit(event) {
      const rect = canvas.getBoundingClientRect();
      cursor.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
      raycaster.setFromCamera(cursor, camera);
      return raycaster.intersectObjects(pickable, false)[0]?.object.userData.id || '';
    }
    canvas.onpointerdown = event => { if (event.isPrimary && event.button === 0) pointer = {x: event.clientX, y: event.clientY, id: event.pointerId}; };
    canvas.onpointerup = event => {
      if (pointer?.id === event.pointerId && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 5) onSelect(hit(event));
      pointer = null;
    };
    canvas.onpointercancel = () => { pointer = null; };
    canvas.onpointermove = event => {
      if (event.buttons) return;
      const next = hit(event);
      if (next !== hover) { hover = next; canvas.style.cursor = hover ? 'pointer' : 'grab'; requestRender(); }
    };
    canvas.onpointerleave = () => { hover = ''; requestRender(); };
    function zoom(factor) {
      userCamera = true; stopJourney(); overview = null; const offset = camera.position.clone().sub(controls.target);
      offset.setLength(Math.max(controls.minDistance, Math.min(controls.maxDistance, offset.length() * factor)));
      camera.position.copy(controls.target).add(offset); controls.update(); requestRender();
    }
    canvas.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', 'Home', 'Escape'].includes(event.key)) return;
      event.preventDefault();
      if (['+', '=', '-'].includes(event.key)) zoom(event.key === '-' ? 1.2 : .8);
      else if (event.key === 'Home') reset();
      else if (event.key === 'Escape') onSelect('');
      else {
        userCamera = true; stopJourney(); overview = null; const offset = camera.position.clone().sub(controls.target);
        const axis = event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? new T.Vector3(0, 1, 0) : new T.Vector3().crossVectors(camera.up, offset).normalize();
        offset.applyAxisAngle(axis, ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -.14 : .14);
        camera.position.copy(controls.target).add(offset); controls.update(); requestRender();
      }
    };
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      if (!disposed) { stage.dataset.renderer = 'unavailable'; onSelect(''); }
    });
    function travelTo(destination, targetDestination, duration = 650, onEnd, arc = .08) {
      const to = {camera: {...destination}, target: {...targetDestination}};
      if (reducedMotion.matches) { applyCamera(to); onEnd?.(); return; }
      focusTravel = {from: cameraState(), to, duration, arc, onEnd, start: pausedAt || performance.now()};
    }
    function focusCamera(id) {
      if (!fitted || !width || !height) return;
      const entry = entries.get(id);
      if (!entry) {
        if (overview) { travelTo(overview.camera, overview.target); overview = null; }
        return;
      }
      stopJourney(); overview ||= {camera: camera.position.clone(), target: controls.target.clone()};
      const right = new T.Vector3(1, 0, 0).applyQuaternion(camera.quaternion), up = new T.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
      const forward = new T.Vector3(0, 0, 1).applyQuaternion(camera.quaternion), relative = entry.mesh.position.clone().sub(camera.position);
      const depth = -relative.dot(forward), tangent = Math.tan(camera.fov * Math.PI / 360);
      const x = width >= 600 ? (width - 320) / 2 : width / 2;
      const y = width >= 600 ? (height - 65) / 2 : Math.max(100, height * .28);
      const dx = relative.dot(right) - (x / width * 2 - 1) * depth * tangent * camera.aspect;
      const dy = relative.dot(up) - (1 - y / height * 2) * depth * tangent;
      const offset = right.multiplyScalar(dx).addScaledVector(up, dy);
      travelTo(camera.position.clone().add(offset), controls.target.clone().add(offset));
    }
    function select(focus) {
      currentFocus = focus;
      if (!focus.selected && journeyIds.size) {
        const neighbors = new Set(journeyIds);
        for (const edge of data.edges) if (journeyIds.has(edge.source) || journeyIds.has(edge.target)) {
          neighbors.add(edge.source); neighbors.add(edge.target);
        }
        focus = {seeds: journeyIds, neighbors};
      }
      const next = focus.selected?.id || '';
      if (next && next !== selected && !reducedMotion.matches) { transition = performance.now(); transitionEnd = transition + 900; }
      if (next !== selected) focusCamera(next);
      selected = next;
      for (const entry of entries.values()) {
        entry.dim = focus.seeds.size > 0 && !focus.neighbors.has(entry.node.id);
        const picked = entry.node.id === selected, neighbor = !!selected && !picked && focus.neighbors.has(entry.node.id);
        const color = nodeColor(entry.node);
        entry.mesh.material.color.set(color); entry.mesh.material.emissive.set(color);
        entry.opacity = entry.dim ? palette.light ? .3 : .28 : 1;
        entry.mesh.material.depthWrite = !entry.dim;
        entry.haloOpacity = entry.dim ? .015 : picked ? .32 : neighbor ? .22 : .12;
        entry.halo.material.color.set(color); entry.outline.material.color.set(picked ? palette.focus : entry.node.conflict ? palette.conflict : entry.node.matched ? palette.match : color);
        entry.outline.visible = picked || !!entry.node.conflict || !!entry.node.matched || ['Topic', 'Tag', 'Claim'].includes(entry.node.kind);
        entry.label.classList.toggle('picked', picked); entry.label.classList.toggle('matched', !!entry.node.matched);
        entry.label.classList.toggle('neighbor', neighbor); entry.label.classList.toggle('dim', entry.dim);
        entry.label.setAttribute('aria-pressed', String(picked));
      }
      for (const item of lines) {
        const {edge, line, arrow} = item, conflict = edge.kind === 'conflict_candidate';
        item.emphasized = focus.seeds.has(edge.source) || focus.seeds.has(edge.target);
        const color = conflict ? palette.conflict : item.emphasized ? selected ? palette.focus : palette.strong : palette.edge;
        line.material.color.set(color);
        item.opacity = item.emphasized ? .83 : focus.seeds.size ? palette.light ? .12 : .045 : conflict ? .65 :
          edge.kind === 'linksTo' ? palette.light ? .57 : .4 : palette.light ? .4 : .23;
        arrow.visible = item.emphasized && !conflict; arrow.material.color.set(color); arrow.material.opacity = .8;
        item.beacon.material.color.set(color);
      }
      canvas.dataset.selected = selected; requestRender();
    }
    function themeChanged() {
      palette = readPalette(); scene.fog.color.set(palette.background);
      const blending = palette.light ? T.NormalBlending : T.AdditiveBlending;
      for (const sprite of [...entries.values()].map(entry => entry.halo).concat(lines.map(item => item.beacon))) {
        sprite.material.blending = blending; sprite.material.needsUpdate = true;
      }
      signal.material.color.set(palette.focus);
      // Recolor the current focus in place, preserving camera, selection, labels and rotation.
      select(currentFocus);
    }
    document.addEventListener('obsi-theme-change', themeChanged);
    select(currentFocus);
    return {
      select, zoom, reset, journey, stopJourney,
      capture() { return {...cameraState(), positions: Object.fromEntries([...entries].map(([id, {mesh}]) =>
        [id, {x: mesh.position.x, y: mesh.position.y, z: mesh.position.z}]))}; },
      labels(value) { labels = value; requestRender(); },
      rotate(value) { stopJourney(); userCamera = true; controls.autoRotate = value; lastTime = performance.now(); requestRender(); },
      visible(value) { visible = value; if (value) resumeMotion(); else pauseMotion(); },
      dispose() {
        disposed = true; stopJourney(); cancelAnimationFrame(frame); clearTimeout(resizeFit); resize.disconnect(); intersection.disconnect(); controls.dispose();
        document.removeEventListener('visibilitychange', visibilityChanged); reducedMotion.removeEventListener('change', motionChanged);
        document.removeEventListener('obsi-theme-change', themeChanged);
        const resources = new Set([glowMap, sphere, ring, diamond, arrowShape]);
        scene.traverse(object => { if (object.geometry) resources.add(object.geometry); if (object.material) resources.add(object.material); });
        for (const resource of resources) resource.dispose();
        renderer.dispose(); renderer.forceContextLoss(); canvas.remove(); labelLayer.remove();
      }
    };
  }
  return {mount};
})();
