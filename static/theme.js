// Apply saved theme immediately (before body paints) to avoid a flash.
(function () {
  try {
    var t = localStorage.getItem('ndx-theme');
    if (t === 'light') document.documentElement.dataset.theme = 'light';
  } catch (e) {}
})();

function toggleTheme() {
  var toLight = document.documentElement.dataset.theme !== 'light';
  if (toLight) document.documentElement.dataset.theme = 'light';
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem('ndx-theme', toLight ? 'light' : 'dark'); } catch (e) {}
  document.querySelectorAll('.theme-toggle').forEach(function (b) { b.textContent = toLight ? '☀' : '☾'; });
  window.dispatchEvent(new Event('themechange'));
}

// Colors for Lightweight Charts (they don't read CSS variables).
function chartColors() {
  var light = document.documentElement.dataset.theme === 'light';
  return light
    ? { bg: '#ffffff', text: '#54606f', grid: '#e6eaf0', border: '#dce2ea' }
    : { bg: '#151d28', text: '#8b97a6', grid: '#1c2530', border: '#26313f' };
}
function applyChartTheme(chart) {
  if (!chart) return;
  var c = chartColors();
  chart.applyOptions({
    layout: { background: { color: c.bg }, textColor: c.text },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border },
  });
}

// Inject a toggle button into every page header automatically.
document.addEventListener('DOMContentLoaded', function () {
  var light = document.documentElement.dataset.theme === 'light';
  var hdr = document.querySelector('header');
  if (hdr && !hdr.querySelector('.theme-toggle')) {
    var b = document.createElement('button');
    b.className = 'theme-toggle';
    b.title = 'Toggle light / dark';
    b.textContent = light ? '☀' : '☾';
    b.onclick = toggleTheme;
    hdr.appendChild(b);
  }
});
