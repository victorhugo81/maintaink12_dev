// Capital Projects Gantt (frappe-gantt). Reads window.PROJECT_GANTT (set by an
// inline nonce'd script in edit_project.html) and draws the timeline into
// #gantt-chart. A project with no dated tasks never gets this element at all
// (see the {% if gantt_tasks %} guard), so this script is a no-op then.
//
// The Gantt tab is a Bootstrap tab-pane, hidden (display:none) unless it's
// the active tab on load. frappe-gantt measures its container's width via
// getBoundingClientRect() at construction time, so building it while hidden
// renders a zero-width/broken chart. Build it immediately only if the Gantt
// tab is already active; otherwise wait for Bootstrap's shown.bs.tab event
// the first time someone clicks into it.
(function () {
  var tasks = window.PROJECT_GANTT || [];
  var el = document.getElementById('gantt-chart');
  if (!el || !tasks.length || typeof Gantt === 'undefined') { return; }

  var rendered = false;
  function render() {
    if (rendered) { return; }
    rendered = true;
    new Gantt(el, tasks, { view_mode: 'Week', language: 'en' });
  }

  var tabButton = document.getElementById('gantt-tab');
  if (!tabButton || tabButton.classList.contains('active')) {
    render();
  } else {
    tabButton.addEventListener('shown.bs.tab', render);
  }
})();
