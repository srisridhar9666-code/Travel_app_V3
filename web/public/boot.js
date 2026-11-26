// Runs before the app, from a file rather than inline: the deployed page's
// Content-Security-Policy allows scripts from this origin only, so an inline
// script never ran there.
(function () {
  // Theme before first paint, otherwise a dark-mode user gets a white flash on
  // every load. The store saves {"state":{"preference":"dark"},...}.
  try {
    var raw = localStorage.getItem('travel-ops-theme');
    var preference = raw ? (JSON.parse(raw).state || {}).preference : null;
    var theme =
      preference === 'light' || preference === 'dark'
        ? preference
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    document.documentElement.dataset.theme = 'light';
  }

  // Phones and tablets default to the desktop layout (1280px width in index.html,
  // matching Chrome's "Desktop site") so ground users immediately see full desktop
  // tables and status controls. Only switch to mobile responsive width if the user
  // explicitly chose the phone layout via the switcher (stored in sriyatra.view).
  try {
    if (localStorage.getItem('sriyatra.view') === 'mobile') {
      var meta = document.querySelector('meta[name="viewport"]');
      if (meta) {
        meta.setAttribute('content', 'width=device-width, initial-scale=1, viewport-fit=cover');
      }
    }
  } catch (e) {
    // Storage blocked: the desktop layout in index.html still applies.
  }
})();
