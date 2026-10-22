// M&O Dashboard charts (Phase 9). Reads window.MO_DASHBOARD (set by an
// inline nonce'd script in dashboard.html) and draws one Chart.js chart
// per <canvas data-chart="key"> found on the page. Same Chart.js bundle the
// ticket dashboard already uses.
(function () {
  var data = window.MO_DASHBOARD || {};
  var charts = data.charts || {};
  var primary = getComputedStyle(document.documentElement).getPropertyValue('--bs-main-color-primary').trim() || '#F3794C';
  var palette = [primary, '#0F766E', '#7C3AED', '#DC2626', '#0891B2', '#4F46E5', '#16A34A', '#64748B', '#A855F7', '#334155', '#94A3B8'];

  var axisOpts = {
    y: { beginAtZero: true, grid: { color: '#e5e5e5', borderDash: [4, 4], drawTicks: false }, ticks: { color: '#737373', padding: 8, font: { size: 11 } } },
    x: { grid: { display: false }, ticks: { color: '#737373', padding: 8, font: { size: 11 }, autoSkip: true, maxRotation: 45 } }
  };

  // Keep gauge thresholds consistent across district health and progress.
  function gaugeColor(val) {
    if (val >= 80) { return '#15803D'; }
    if (val >= 65) { return '#2563EB'; }
    if (val >= 45) { return '#D97706'; }
    return '#B91C1C';
  }

  function buildGauge(canvas, spec) {
    var rawValue = spec.datasets[0] && spec.datasets[0].data[0];
    var hasValue = rawValue !== null && rawValue !== undefined && Number.isFinite(Number(rawValue));
    var val = hasValue ? Math.max(0, Math.min(100, Number(rawValue))) : 0;
    var displayValue = hasValue ? Math.round(val) + '%' : 'N/A';
    var remainingColor = '#E2E8F0';
    new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: { datasets: [{ data: [val, 100 - val], backgroundColor: [hasValue ? gaugeColor(val) : remainingColor, remainingColor], borderWidth: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        circumference: 270, rotation: 225, cutout: '78%',
        plugins: { legend: { display: false }, tooltip: { enabled: false } }
      },
      plugins: [{
        id: 'gaugeCenterText',
        afterDraw: function (chart) {
          var ctx = chart.ctx, w = chart.width, h = chart.height;
          ctx.save();
          ctx.font = '700 ' + Math.round(h * 0.17) + 'px Inter, sans-serif';
          ctx.fillStyle = '#0F172A';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(displayValue, w / 2, h / 2 + h * 0.06);
          ctx.restore();
        }
      }]
    });
  }

  function build(canvas) {
    var key = canvas.getAttribute('data-chart');
    var spec = charts[key];
    if (!spec) { return; }
    var type = canvas.getAttribute('data-type') || 'bar';
    if (type === 'gauge') { buildGauge(canvas, spec); return; }
    var multi = spec.datasets.length > 1;
    var datasets = spec.datasets.map(function (ds, i) {
      var color = palette[i % palette.length];
      var base = { label: ds.label, data: ds.data, borderWidth: 2 };
      if (type === 'doughnut' || type === 'pie') {
        base.backgroundColor = spec.labels.map(function (_, j) { return palette[j % palette.length]; });
        base.borderWidth = 1;
      } else if (type === 'line') {
        base.borderColor = color; base.backgroundColor = color; base.pointBackgroundColor = color;
        base.tension = 0.3; base.fill = false; base.spanGaps = true;
      } else {
        base.backgroundColor = multi ? color : primary; base.borderRadius = 4; base.borderWidth = 0; base.barThickness = 'flex';
      }
      return base;
    });
    new Chart(canvas.getContext('2d'), {
      type: type,
      data: { labels: spec.labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: multi || type === 'doughnut' || type === 'pie', position: 'bottom', labels: { font: { size: 11 } } } },
        interaction: { intersect: false, mode: 'index' },
        scales: (type === 'doughnut' || type === 'pie') ? {} : axisOpts
      }
    });
  }

  document.querySelectorAll('canvas[data-chart]').forEach(build);

  // Filter form: every select except "preset" uses the generic
  // [data-autosubmit] wiring in includes/footer.html. "preset" needs its
  // own listener (toggle the custom-date inputs; skip auto-submit while
  // "custom" is selected so the user can type dates first) and must NOT
  // also carry data-autosubmit, or footer.html's delegated listener would
  // submit the form on that same change too, defeating the skip.
  //
  // This split matters: a per-select listener calling form.requestSubmit()
  // races with includes/footer.html's unsaved-changes watcher, which
  // listens for "change" on the form itself. Because requestSubmit()
  // synchronously fires a nested "submit" event (clearing the watcher's
  // dirty flag) *before* the original "change" event finishes bubbling
  // from the select up to the form (which sets the flag right back to
  // true), the watcher's dirty flag can end up true again by the time the
  // navigation actually happens — spuriously triggering the "Leave site?"
  // beforeunload prompt. Routing non-preset selects through
  // [data-autosubmit] avoids this: that attribute makes the whole form
  // untrackable (see footer.html's isTrackable()), so the watcher never
  // listens on it in the first place.
  var form = document.getElementById('dashboard-filters');
  if (form) {
    var preset = form.querySelector('[name="preset"]');
    var custom = form.querySelector('#custom-range');
    function toggleCustom() { if (custom) { custom.hidden = preset.value !== 'custom'; } }
    toggleCustom();
    if (preset) {
      preset.addEventListener('change', function () {
        toggleCustom();
        if (preset.value !== 'custom') { form.requestSubmit(); }
      });
    }
  }
})();
