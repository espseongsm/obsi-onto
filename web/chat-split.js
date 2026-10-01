/* Store a preferred split; smaller windows may temporarily constrain that ratio. */
(() => {
  const DEFAULT = 1 / 3, KEY = 'obsi-chat-ratio';
  const dock = document.querySelector('#page-ask'), main = document.querySelector('#chat-main');
  const graph = document.querySelector('#chat-graph-panel'), handle = document.querySelector('#chat-splitter');
  const readout = document.querySelector('#split-readout'), reset = document.querySelector('#split-reset');
  const desktop = matchMedia('(min-width:1101px)');
  let preferred = DEFAULT, effective = DEFAULT, drag = null, pending = null, frame = 0;
  try {
    const saved = Number(localStorage.getItem(KEY));
    if (Number.isFinite(saved) && saved >= .25 && saved <= .65) preferred = saved;
  } catch {}

  function bounds() {
    const box = main.getBoundingClientRect(), width = box.width + graph.getBoundingClientRect().width;
    return {left: box.left, width,
      min: width ? Math.min(DEFAULT, Math.max(.25, 280 / width)) : .25,
      max: width ? Math.max(DEFAULT, Math.min(.65, 1 - 360 / width)) : .65};
  }
  function draw() {
    const limits = bounds();
    effective = desktop.matches && limits.width ? Math.max(limits.min, Math.min(limits.max, preferred)) : preferred;
    dock.style.setProperty('--chat-column', effective + 'fr');
    dock.style.setProperty('--graph-column', (1 - effective) + 'fr');
    const percent = Math.round(effective * 100);
    handle.setAttribute('aria-valuemin', String(Math.round(limits.min * 100)));
    handle.setAttribute('aria-valuemax', String(Math.round(limits.max * 100)));
    handle.setAttribute('aria-valuenow', String(percent));
    handle.setAttribute('aria-valuetext', `Chat ${percent}%, knowledge graph ${100 - percent}%`);
    readout.textContent = `Chat ${percent} · Graph ${100 - percent}`;
    reset.disabled = Math.abs(preferred - DEFAULT) < .0001;
  }
  function save() {
    try {
      if (Math.abs(preferred - DEFAULT) < .0001) localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, String(preferred));
    } catch {}
  }
  function flush() {
    cancelAnimationFrame(frame); frame = 0;
    if (pending === null) return;
    const limits = bounds();
    preferred = Math.max(limits.min, Math.min(limits.max, pending)); pending = null;
    draw();
  }
  function finish(cancel = false) {
    if (!drag) return;
    const previous = drag;
    flush(); drag = null;
    if (cancel) preferred = previous.original;
    document.body.classList.remove('resizing-split');
    if (handle.hasPointerCapture(previous.id)) handle.releasePointerCapture(previous.id);
    draw();
    if (!cancel) save();
  }
  function restore() {
    finish(true); preferred = DEFAULT; draw(); save();
  }
  handle.addEventListener('pointerdown', event => {
    if (!desktop.matches || !event.isPrimary || event.button !== 0) return;
    event.preventDefault(); handle.focus({preventScroll: true});
    const box = main.getBoundingClientRect();
    drag = {id: event.pointerId, original: preferred, offset: event.clientX - box.right};
    document.body.classList.add('resizing-split');
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', event => {
    if (drag?.id !== event.pointerId) return;
    const limits = bounds();
    if (!limits.width) return;
    pending = (event.clientX - limits.left - drag.offset) / limits.width;
    if (!frame) frame = requestAnimationFrame(flush);
  });
  handle.addEventListener('pointerup', event => { if (drag?.id === event.pointerId) finish(); });
  handle.addEventListener('pointercancel', () => finish(true));
  handle.addEventListener('lostpointercapture', () => finish(true));
  handle.addEventListener('dblclick', restore);
  handle.addEventListener('keydown', event => {
    if (event.key === 'Escape' && drag) { event.preventDefault(); finish(true); return; }
    if (drag || !['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Home') { restore(); return; }
    pending = effective + (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? .1 : .02);
    flush(); save();
  });
  reset.addEventListener('click', restore);
  window.addEventListener('blur', () => finish(true));
  desktop.addEventListener('change', () => { finish(true); draw(); });
  new ResizeObserver(draw).observe(dock);
  draw();
})();
