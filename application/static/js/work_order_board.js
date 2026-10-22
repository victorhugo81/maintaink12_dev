// Work Orders Kanban board: native HTML5 drag-and-drop, mirroring
// project_board.js exactly (no fetch/JSON, one hidden per-card form
// submitted via requestSubmit()). The one real difference: WorkOrder.status
// is governed by workflow.py's transition matrix, not a free 3-value field,
// so there is NO client-side check here on which columns a card may enter —
// every drop is submitted and the server (workflow.allowed_transitions() /
// apply_transition() in update_work_order_status_board()) decides. An
// invalid drop flashes an error and the card simply stays put on reload.
(function () {
  var board = document.getElementById('work-order-board');
  if (!board) { return; }

  var draggedId = null;

  board.querySelectorAll('.board-card[draggable="true"]').forEach(function (card) {
    card.addEventListener('dragstart', function (e) {
      draggedId = card.dataset.woId;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', draggedId);
    });
  });

  board.querySelectorAll('.board-column').forEach(function (col) {
    col.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
    });
    col.addEventListener('drop', function (e) {
      e.preventDefault();
      var id = draggedId || e.dataTransfer.getData('text/plain');
      if (!id) { return; }
      var form = document.getElementById('wo-board-status-form-' + id);
      if (!form) { return; }
      var statusInput = form.querySelector('input[name="status"]');
      var newStatus = col.dataset.status;
      if (statusInput.value === newStatus) { return; } // dropped back in its own column
      statusInput.value = newStatus;
      form.requestSubmit();
    });
  });
})();
