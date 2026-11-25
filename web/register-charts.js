/* Register charts: records by month (instead of by day) and records by month split by sex.
 * Loaded at the end of every *-register.html page; it replaces the page's trend() chart and adds the
 * by-sex panel next to it. Also tells the portal how tall the page is, so nothing gets cut off.
 */
(function () {
  'use strict';
  var FEMALE = '#c0367a', MALE = '#2563eb', TOTAL = '#2f855a';
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var last = null;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function nf(n) { return Number(n).toLocaleString('en-GB'); }
  function label(m) { return MONTHS[+m.slice(5, 7) - 1] + ' ' + m.slice(2, 4); }
  function longLabel(m) { return MONTHS[+m.slice(5, 7) - 1] + ' ' + m.slice(0, 4); }
  function months(a, b) { var out = [], y = +a.slice(0, 4), m = +a.slice(5, 7); while (y * 12 + m <= +b.slice(0, 4) * 12 + +b.slice(5, 7)) { out.push(y + '-' + String(m).padStart(2, '0')); m++; if (m > 12) { m = 1; y++; } } return out; }
  function nice(v) { if (v <= 4) return 4; if (v <= 40) return Math.ceil(v / 4) * 4; var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; }

  // Bars for one or more series over months.
  function barChart(cats, series) {
    var W = 640, H = 210, L = 40, T = 14, R = 6, B = 24, pw = W - L - R, ph = H - T - B;
    var max = nice(Math.max(1, Math.max.apply(null, series.reduce(function (a, s) { return a.concat(s.values); }, []))));
    var step = pw / Math.max(1, cats.length), gw = step * (series.length > 1 ? .78 : .66), bw = gw / series.length;
    var y = function (v) { return T + ph - v / max * ph; };
    var showValues = cats.length * series.length <= 26, every = Math.ceil(cats.length / 13);
    var g = [0, .25, .5, .75, 1].map(function (f) { return '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(max * f) + '" y2="' + y(max * f) + '" stroke="#e7efeb"/><text x="' + (L - 6) + '" y="' + y(max * f) + '" text-anchor="end" dominant-baseline="central" font-size="10" fill="#6b8580">' + nf(max * f) + '</text>'; }).join('');
    var bars = series.map(function (s, j) {
      return s.values.map(function (v, k) {
        var x = L + step * k + (step - gw) / 2 + bw * j;
        return '<rect x="' + x + '" y="' + y(v) + '" width="' + Math.max(1, bw - 1.5) + '" height="' + Math.max(0, T + ph - y(v)) + '" rx="2.5" fill="' + s.color + '"><title>' + esc(longLabel(cats[k])) + (series.length > 1 ? ' · ' + esc(s.name) : '') + ': ' + nf(v) + '</title></rect>' +
          (showValues && v ? '<text x="' + (x + (bw - 1.5) / 2) + '" y="' + (y(v) - 4) + '" text-anchor="middle" font-size="9.5" fill="#46625b">' + nf(v) + '</text>' : '');
      }).join('');
    }).join('');
    var xl = cats.map(function (c, k) { return k % every ? '' : '<text x="' + (L + step * k + step / 2) + '" y="' + (H - 7) + '" text-anchor="middle" font-size="10" fill="#526d75">' + label(c) + '</text>'; }).join('');
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto;display:block" role="img">' + g + '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + (T + ph) + '" y2="' + (T + ph) + '" stroke="#c9d9d2"/>' + bars + xl + '</svg>';
  }
  function legend(items) { return '<div class="rc-legend">' + items.map(function (i) { return '<span><i style="background:' + i[1] + '"></i>' + esc(i[0]) + '</span>'; }).join('') + '</div>'; }
  function table(head, rows) {
    return '<details class="rc-table"><summary>View monthly counts</summary><table><thead><tr>' + head.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr>' + r.map(function (v, i) { return '<td>' + (i ? nf(v) : esc(v)) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></details>';
  }

  var css = document.createElement('style');
  css.textContent = '.chart-grid{grid-template-columns:1fr 1fr!important}.chart-grid>.chart-panel{grid-column:auto!important}@media(max-width:900px){.chart-grid{grid-template-columns:1fr!important}}' +
    '.rc-legend{display:flex;gap:14px;font-size:11px;color:#46625b;margin:-8px 0 6px}.rc-legend span{display:inline-flex;align-items:center;gap:5px}.rc-legend i{width:10px;height:10px;border-radius:3px}' +
    '.rc-table{margin-top:8px;font-size:11.5px}.rc-table summary{cursor:pointer;color:#2f6b52}.rc-table table{border-collapse:collapse;margin-top:6px;width:100%}' +
    '.rc-table th,.rc-table td{padding:3px 8px;border-bottom:1px solid #e7efeb;text-align:right}.rc-table th:first-child,.rc-table td:first-child{text-align:left}' +
    '.rc-sexcards{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}' +
    '.rc-sexcard{display:flex;gap:10px;align-items:center;border:1px solid #e3ece8;border-radius:12px;padding:10px 12px;color:var(--c)}' +
    '.rc-sexcard .ic{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--c) 12%,#fff)}' +
    '.rc-sexcard b{display:block;font-size:20px;color:#14302a;letter-spacing:-.3px}.rc-sexcard small{display:block;font-size:11px;color:#58737a}' +
    '.rc-split{display:flex;height:8px;border-radius:5px;overflow:hidden;background:#edf3f0;margin-bottom:12px;gap:2px}.rc-split i{display:block;height:100%}' +
    '.rc-sexrow{display:grid;grid-template-columns:28px 92px 1fr 88px;gap:8px;align-items:center;font-size:12px;color:#244a42;margin:6px 0}' +
    '.rc-sexrow .ic{display:grid;place-items:center}.rc-sexrow .ic svg{width:20px;height:20px}.rc-sexrow .tr{height:8px;border-radius:5px;background:#edf3f0;overflow:hidden}.rc-sexrow .tr i{display:block;height:100%;border-radius:5px}' +
    '.rc-sexrow .v{text-align:right;font-weight:600}.rc-sexrow small{color:#6b8580;font-weight:500}' +
    '.rc-empty{color:#6b8580;font-size:12px;padding:30px 0;text-align:center}';
  document.head.appendChild(css);

  function sexPanel(trendTarget) {
    var panel = document.getElementById('rcSexPanel');
    if (!panel) {
      var host = trendTarget.closest('.chart-panel'); if (!host) return null;
      panel = document.createElement('section'); panel.className = 'card chart-panel'; panel.id = 'rcSexPanel';
      var what = (host.querySelector('h3') || {}).textContent || 'Records over time';
      panel.innerHTML = '<h3>' + esc(what.replace(/ over time$/i, '')) + ' by month and sex</h3><p>Female and male, per month</p><div id="rcSexChart"></div>';
      host.insertAdjacentElement('afterend', panel);
    }
    return document.getElementById('rcSexChart');
  }

  function install() {
    if (typeof window.trend !== 'function' || typeof window.normalizeSummary !== 'function') return;
    var normal = window.normalizeSummary;
    window.normalizeSummary = function (payload) { var r = normal(payload); last = payload; return r; };
    window.trend = function (target, rows) {
      var panel = target.closest('.chart-panel'), p = panel && panel.querySelector('p');
      if (p) p.textContent = p.textContent.replace(/by day/i, 'by month').replace(/ · Days without records shown as zero/, '');
      var byMonth = {};
      (rows || []).forEach(function (r) { var m = String(r.label).slice(0, 7); byMonth[m] = (byMonth[m] || 0) + (+r.count || 0); });
      var sexRows = (last && last.monthlyBySex) || [];
      var keys = Object.keys(byMonth).concat(sexRows.map(function (r) { return r.month; })).sort();
      var target2 = sexPanel(target);
      if (!keys.length) { target.innerHTML = '<div class="rc-empty">No records in this period.</div>'; if (target2) target2.innerHTML = '<div class="rc-empty">No records in this period.</div>'; return; }
      var ms = months(keys[0], keys[keys.length - 1]);
      var totals = ms.map(function (m) { return byMonth[m] || 0; });
      target.innerHTML = barChart(ms, [{ name: 'Records', color: TOTAL, values: totals }]) +
        table(['Month', 'Records'], ms.map(function (m, i) { return [longLabel(m), totals[i]]; }));
      if (target2) {
        var map = {}; sexRows.forEach(function (r) { map[r.month] = r; });
        var f = ms.map(function (m) { return (map[m] || {}).female || 0; }), mm = ms.map(function (m) { return (map[m] || {}).male || 0; });
        var other = ms.map(function (m) { return (map[m] || {}).other || 0; }), hasOther = other.some(Boolean);
        var series = [{ name: 'Female', color: FEMALE, values: f }, { name: 'Male', color: MALE, values: mm }];
        if (hasOther) series.push({ name: 'Not recorded', color: '#94a3b8', values: other });
        target2.innerHTML = legend(series.map(function (s) { return [s.name + ' (' + nf(s.values.reduce(function (a, b) { return a + b; }, 0)) + ')', s.color]; })) + barChart(ms, series) +
          table(['Month', 'Female', 'Male'].concat(hasOther ? ['Not recorded'] : []), ms.map(function (m, i) { return [longLabel(m), f[i], mm[i]].concat(hasOther ? [other[i]] : []); }));
      }
      postHeight();
    };
  }


  // ---------------------------------------------------------------- "Sex distribution" panel: icons and the sex colours
  var ICONS = {
    female: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><circle cx="12" cy="4.2" r="2.6" fill="currentColor"/><path d="M12 7.6c-1.9 0-3.1 1-3.6 2.7L6.6 16h3.1l-.4 6h5.4l-.4-6h3.1l-1.8-5.7C15.1 8.6 13.9 7.6 12 7.6z" fill="currentColor"/></svg>',
    male: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><circle cx="12" cy="4.2" r="2.6" fill="currentColor"/><path d="M9.2 7.8h5.6c1.3 0 2.2 1 2.2 2.2v5.6h-2v6.4h-2.1v-6h-1.8v6H9v-6.4H7V10c0-1.2.9-2.2 2.2-2.2z" fill="currentColor"/></svg>',
    other: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><circle cx="12" cy="4.2" r="2.6" fill="currentColor"/><path d="M9.2 7.8h5.6c1.3 0 2.2 1 2.2 2.2v5.6h-2v6.4H9v-6.4H7V10c0-1.2.9-2.2 2.2-2.2z" fill="currentColor"/></svg>'
  };
  function kindOf(label) { var t = String(label).toUpperCase(); return t === 'FEMALE' ? 'female' : t === 'MALE' ? 'male' : 'other'; }
  function nameOf(label) { var k = kindOf(label); if (k !== 'other') return k === 'female' ? 'Female' : 'Male'; var t = String(label); return /unknown|not recorded/i.test(t) ? 'Not recorded' : t.charAt(0) + t.slice(1).toLowerCase(); }
  function sexBars(target, rows) {
    var total = rows.reduce(function (a, r) { return a + (+r.count || 0); }, 0);
    if (!rows.length || !total) { target.innerHTML = '<div class="rc-empty">No records in this period.</div>'; return; }
    var order = { female: 0, male: 1, other: 2 };
    rows = rows.slice().sort(function (a, b) { return order[kindOf(a.label)] - order[kindOf(b.label)] || b.count - a.count; });
    var max = Math.max.apply(null, rows.map(function (r) { return +r.count || 0; }).concat([1]));
    var colour = { female: FEMALE, male: MALE, other: '#94a3b8' };
    var main = rows.filter(function (r) { return kindOf(r.label) !== 'other'; });
    target.innerHTML = '<div class="rc-sexcards">' + main.map(function (r) {
      var k = kindOf(r.label), pct = Math.round(r.count / total * 100);
      return '<div class="rc-sexcard" style="--c:' + colour[k] + '"><span class="ic">' + ICONS[k] + '</span><span><b>' + nf(r.count) + '</b><small>' + nameOf(r.label) + ' · ' + pct + '%</small></span></div>';
    }).join('') + '</div>' +
      '<div class="rc-split">' + rows.map(function (r) { var k = kindOf(r.label); return '<i style="width:' + (r.count / total * 100) + '%;background:' + colour[k] + '" title="' + esc(nameOf(r.label)) + ': ' + nf(r.count) + '"></i>'; }).join('') + '</div>' +
      rows.map(function (r) {
        var k = kindOf(r.label), pct = r.count / total * 100;
        return '<div class="rc-sexrow"><span class="ic" style="color:' + colour[k] + '">' + ICONS[k] + '</span><span class="nm">' + esc(nameOf(r.label)) + '</span>' +
          '<span class="tr"><i style="width:' + (r.count / max * 100) + '%;background:' + colour[k] + '"></i></span><span class="v">' + nf(r.count) + ' <small>' + (pct < 1 && r.count ? '<1' : Math.round(pct)) + '%</small></span></div>';
      }).join('');
    postHeight();
  }
  function installBars() {
    if (typeof window.bars !== 'function') return;
    var plain = window.bars;
    // Only when the rows really are sexes (the ANC page reuses this slot for ages).
    var isSex = function (rows) { return rows && rows.length && rows.some(function (r) { return /^(fe)?male$/i.test(String(r.label).trim()); }) && rows.every(function (r) { return /^(fe)?male$|^intersex$|unknown|not recorded|other/i.test(String(r.label).trim()); }); };
    window.bars = function (target, rows) { return target && target.id === 'sexChart' && isSex(rows) ? sexBars(target, rows) : plain(target, rows); };
  }

  // The portal sizes this page's frame to fit it.
  function postHeight() { if (window.parent !== window) window.parent.postMessage({ type: 'register-height', height: document.documentElement.scrollHeight }, '*'); }
  install(); installBars();
  if (window.ResizeObserver) new ResizeObserver(postHeight).observe(document.body);
  window.addEventListener('load', postHeight);
})();
