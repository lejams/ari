/* ARI colour theme. Follows the system unless the visitor chose light or dark with a
   [data-theme-toggle] button; the choice is kept in localStorage and is per browser.
   Loaded synchronously in <head> so the page never flashes the wrong theme.
   Keep backoffice-web/theme.js identical (the back-office is another origin). */
(function () {
  var KEY = 'ari-theme';
  var root = document.documentElement;
  var query = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function stored() {
    try { var v = localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : null; } catch (e) { return null; }
  }
  function effective() {
    return stored() || (query && query.matches ? 'dark' : 'light');
  }
  function sync() {
    var dark = effective() === 'dark';
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) buttons[i].setAttribute('aria-pressed', dark ? 'true' : 'false');
    var meta = document.querySelector('meta[name=theme-color]');
    if (meta) meta.setAttribute('content', dark ? '#060a14' : '#ffffff');
    document.dispatchEvent(new CustomEvent('ari-theme', { detail: effective() }));
  }
  function apply() {
    var choice = stored();
    if (choice) root.setAttribute('data-theme', choice); else root.removeAttribute('data-theme');
    sync();
  }
  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest || !target.closest('[data-theme-toggle]')) return;
    try { localStorage.setItem(KEY, effective() === 'dark' ? 'light' : 'dark'); } catch (e) { /* the choice just is not kept */ }
    apply();
  });
  if (query && query.addEventListener) query.addEventListener('change', sync);
  document.addEventListener('DOMContentLoaded', sync);
  apply();
})();
