/* Compact evidence tiles share one full, accessible source snapshot dialog. */
const SourceCards = (() => {
  const entries = new WeakMap();
  let dialog, returnFocus;

  function highlight(prefix, citation, root = document) {
    root.querySelectorAll('.source-card.selected-source').forEach(card => {
      card.classList.remove('selected-source');
      card.removeAttribute('aria-current');
    });
    const card = citation && document.getElementById(prefix + citation);
    if (card && root.contains(card)) {
      card.classList.add('selected-source');
      card.setAttribute('aria-current', 'true');
    }
  }
  function getDialog() {
    if (dialog) return dialog;
    dialog = el('dialog', undefined, 'source-dialog');
    dialog.id = 'source-detail-dialog';
    dialog.setAttribute('aria-labelledby', 'source-detail-title');
    dialog.addEventListener('close', () => {
      if (returnFocus?.isConnected) returnFocus.focus({preventScroll: true});
    });
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right ||
          event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
    document.body.append(dialog);
    return dialog;
  }
  function open(id, trigger) {
    const card = document.getElementById(id);
    const entry = card && entries.get(card);
    if (!entry) return false;
    const {item, result, focusGraph} = entry;
    returnFocus = trigger || card;
    focusGraph(item, false);
    highlight('source-' + result.id + '-', item.citation);
    const modal = getDialog();
    const header = el('header', undefined, 'source-dialog-header');
    const title = el('div');
    title.append(el('span', item.citation + ' · Source evidence', 'source-dialog-eyebrow'));
    const heading = el('h2', item.heading || item.path.split('/').at(-1).replace(/\.md$/i, ''));
    heading.id = 'source-detail-title';
    title.append(heading);
    const close = el('button', '×', 'source-dialog-close');
    close.type = 'button'; close.setAttribute('aria-label', 'Close source details');
    close.autofocus = true;
    close.onclick = () => modal.close();
    header.append(title, close);

    const body = el('div', undefined, 'source-dialog-body');
    body.append(el('p', item.path, 'source-dialog-path'));
    const metadata = el('dl', undefined, 'source-detail-meta');
    for (const [label, value] of [['Source location', `Lines ${item.start}–${item.end}`],
      ['Recorded date', item.record_date || 'Unknown'], ['Saved version', item.revision],
      ['Search routes', item.routes.map(uiText).join(' · ') || '—']]) {
      const group = el('div'); group.append(el('dt', label), el('dd', String(value))); metadata.append(group);
    }
    body.append(metadata, el('p', 'This source was saved with the answer. It may differ from the current note.', 'source-snapshot-note'),
      el('pre', item.text, 'source-detail-text'));
    const fingerprint = el('details', undefined, 'source-fingerprint');
    fingerprint.append(el('summary', 'Snapshot details'), el('p', 'Source hash · ' + item.hash));
    body.append(fingerprint);

    const footer = el('footer', undefined, 'source-dialog-actions');
    const obsidian = el('a', 'Open in Obsidian ↗'); obsidian.href = item.uri;
    footer.append(obsidian);
    if (result.graph) {
      const showGraph = el('button', 'View in graph', 'primary'); showGraph.type = 'button';
      showGraph.onclick = () => { modal.close(); focusGraph(item, true); };
      footer.append(showGraph);
    }
    modal.replaceChildren(header, body, footer);
    if (!modal.open) modal.showModal();
    else close.focus();
    return true;
  }
  function create(result, focusGraph) {
    const section = el('details', undefined, 'chat-sources source-tiles');
    const header = el('summary');
    header.append(el('span', 'Sources'), el('span', String(result.evidence.length), 'source-count'));
    section.append(header);
    if (!result.evidence.length) { section.append(el('p', 'No source evidence to display.', 'hint')); return section; }
    section.append(el('p', 'Select a card to read the full source.', 'source-tiles-hint'));
    const grid = el('div', undefined, 'source-tile-grid');
    for (const item of result.evidence) {
      const card = el('button', undefined, 'source-card source-tile'); card.type = 'button';
      card.id = 'source-' + result.id + '-' + item.citation;
      card.setAttribute('aria-haspopup', 'dialog');
      card.setAttribute('aria-controls', 'source-detail-dialog');
      const name = item.path.split('/').at(-1).replace(/\.md$/i, '');
      card.setAttribute('aria-label', `${item.citation} · ${name}${item.heading ? ' · ' + item.heading : ''} · View source details`);
      const top = el('span', undefined, 'source-tile-top');
      top.append(el('span', item.citation, 'source-tile-citation'), el('span', '↗', 'source-tile-arrow'));
      card.append(top, el('strong', name, 'source-tile-title'));
      if (item.heading) card.append(el('span', item.heading, 'source-tile-heading'));
      card.append(el('span', item.text.replace(/\s+/g, ' ').trim().slice(0, 150), 'source-tile-preview'),
        el('span', `Lines ${item.start}–${item.end} · ${item.record_date || 'Date unknown'}`, 'source-tile-foot'));
      card.title = item.path + (item.heading ? ' › ' + item.heading : '');
      entries.set(card, {item, result, focusGraph});
      card.onclick = () => open(card.id, card);
      grid.append(card);
    }
    section.append(grid);
    return section;
  }
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href^="#source-"]');
    if (link && open(link.getAttribute('href').slice(1), link)) event.preventDefault();
  });
  return {create, open, highlight};
})();
