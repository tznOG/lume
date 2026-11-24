'use strict';
(() => {
  const key = 'lume-theme';
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const choices = ['system', 'light', 'dark'];
  let preference = 'system';
  try { preference = localStorage.getItem(key) || 'system'; } catch {}
  if (!choices.includes(preference)) preference = 'system';
  function apply() {
    const theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#141c19' : '#173d35');
    document.querySelectorAll('.theme-select').forEach(select => select.value = preference);
  }
  function change(value) {
    preference = choices.includes(value) ? value : 'system';
    try { localStorage.setItem(key, preference); } catch {}
    apply();
  }
  apply();
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('.theme-select').forEach(select => select.addEventListener('change', event => change(event.target.value)));
  });
  media.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => { if (event.key === key) { preference = choices.includes(event.newValue) ? event.newValue : 'system'; apply(); } });
})();
