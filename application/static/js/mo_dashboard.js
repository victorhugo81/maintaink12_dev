// M&O Dashboard charts (Phase 9). Reads window.MO_DASHBOARD (set by an
// inline nonce'd script in mo_dashboard.html) and draws one Chart.js chart
// per <canvas data-chart="key"> found on the page. Same Chart.js bundle the
// ticket dashboard already uses.
(function () {
  var data = window.MO_DASHBOARD || {};
  var charts = data.charts || {};
  var primary = getComputedStyle(document.documentElement).getPropertyValue('--bs-main-color-primary').trim() || '#12707F';
  var palette = [primary, '#f5a623', '#d0021b', '#7ed321', '#4a90e2', '#9013fe', '#50e3c2', '#b8e986', '#8b572a', '#417505', '#9b9b9b'];

  var axisOpts = {
    y: { beginAtZero: true, grid: { color: '#e5e5e5', borderDash: [4, 4], drawTicks: false }, ticks: { color: '#737373', padding: 8, font: { size: 11 } } },
    x: { grid: { display: false }, ticks: { color: '#737373', padding: 8, font: { size: 11 }, autoSkip: true, maxRotation: 45 } }
  };

  function build(canvas) {
    var key = canvas.getAttribute('data-chart');
    var spec = charts[key];
    if (!spec) { return; }
    var type = canvas.getAttribute('data-type') || 'bar';
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

  // Filter form: submit on any select change; show custom date inputs only for the custom preset.
  var form = document.getElementById('dashboard-filters');
  if (form) {
    var preset = form.querySelector('[name="preset"]');
    var custom = form.querySelector('#custom-range');
    function toggleCustom() { if (custom) { custom.hidden = preset.value !== 'custom'; } }
    toggleCustom();
    form.querySelectorAll('select').forEach(function (sel) {
      sel.addEventListener('change', function () {
        if (sel === preset) { toggleCustom(); if (preset.value === 'custom') { return; } }
        form.submit();
      });
    });
  }
})();
