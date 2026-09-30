/* Restore appearance before first paint; graph scenes respond without rebuilding. */
const AppTheme = (() => {
  const modes = ['light', 'dark', 'ai'];
  let saved;
  try { saved = localStorage.getItem('obsi-theme'); } catch {}
  let current = modes.includes(saved) ? saved : 'ai';
  document.documentElement.dataset.theme = current;
  function apply(mode) {
    if (!modes.includes(mode)) return;
    current = mode;
    document.documentElement.dataset.theme = mode;
    try { localStorage.setItem('obsi-theme', mode); } catch {}
    for (const button of document.querySelectorAll('[data-theme-mode]')) {
      button.setAttribute('aria-pressed', String(button.dataset.themeMode === mode));
    }
    document.dispatchEvent(new CustomEvent('obsi-theme-change', {detail: {theme: mode}}));
  }
  document.addEventListener('DOMContentLoaded', () => {
    for (const button of document.querySelectorAll('[data-theme-mode]')) {
      button.setAttribute('aria-pressed', String(button.dataset.themeMode === current));
      button.onclick = () => apply(button.dataset.themeMode);
    }
  });
  return {apply};
})();
