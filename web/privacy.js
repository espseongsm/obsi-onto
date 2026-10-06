/* Model recipients and explicit, optional local-history retention controls. */
const PrivacyUI = (() => {
  let retentionDays;
  const size = bytes => {
    const value = Math.max(0, Number(bytes) || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  };
  function render(data) {
    const channels = data.transmission || {};
    const target = $('#transmission-info');
    target.replaceChildren();
    for (const [key, title] of [['embedding', 'Semantic search'], ['generation', 'Answers & relationship checks'], ['suggestions', 'Suggested questions']]) {
      const channel = channels[key];
      if (!channel) continue;
      target.append(el('h4', title), el('p', channel.enabled ?
        `${channel.external ? 'External provider' : 'Local processing'} · ${channel.recipient}` : 'Model transmission is off.'));
      if (channel.enabled) target.append(el('p', channel.scope, 'hint'));
    }
    const external = Object.values(channels).filter(channel => channel.enabled && channel.external);
    $('#transmission-notice').hidden = !external.length;
    $('#transmission-notice').textContent = external.length ?
      'External model processing is enabled. ' + external.map(channel => `${channel.recipient}: ${channel.scope}`).join(' ') : '';
    const usage = data.storage_usage || {};
    const history = data.history || {};
    $('#storage-usage').replaceChildren(
      el('p', `Local index & history: ${size((usage.database_bytes || 0) + (usage.wal_bytes || 0))}`),
      el('p', `Downloaded search model files: ${size(usage.models_bytes)}`),
      el('p', `${history.conversations || 0} conversations · ${history.runs || 0} saved answers`, 'hint'));
    if (history.storage_warning) $('#storage-usage').append(el('p', history.storage_warning, 'hint'));
    $('#retention-info').textContent = history.retention_days ?
      `Answers and their saved source passages older than ${history.retention_days} days are deleted automatically, including related question records.` :
      'All conversations and saved source passages are kept until you choose to delete them.';
    if (retentionDays !== history.retention_days) {
      retentionDays = history.retention_days ?? 0;
      $('#retention-days').value = String(retentionDays);
    }
  }
  async function reloadHistory() {
    ChatUI.fresh(); currentRun = null;
    await refresh(); await history();
    if (status?.vault) await QueryUI.restore();
  }
  function bind() {
    $('#retention-form').onsubmit = event => {
      event.preventDefault();
      action(event.submitter, async () => {
        const days = Number($('#retention-days').value);
        if (days && !confirm(`Delete saved answers and source passages older than ${days} days now, and keep deleting older history automatically? Your notes and index will be preserved.`)) return;
        await api('/history/retention', 'POST', {days});
        if (days) await reloadHistory(); else await refresh();
        toast(days ? `History retention set to ${days} days.` : 'All history will be kept.');
      });
    };
    $('#cleanup-history').onclick = event => action(event.currentTarget, async () => {
      const days = Number($('#cleanup-days').value);
      if (!confirm(`Delete saved answers and source passages older than ${days} days? Your notes, index and recent history will be preserved.`)) return;
      const result = await api('/history/cleanup', 'POST', {days});
      await reloadHistory();
      toast(`Deleted ${result.deleted_runs} saved answers and ${result.deleted_jobs} question records. Freed ${size(result.reclaimed_bytes)}.`);
    });
  }
  return {render, bind};
})();
