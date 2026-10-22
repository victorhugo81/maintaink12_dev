// Capital Projects Kanban board: native HTML5 drag-and-drop (no library, no
// fetch/JSON — this app has neither anywhere else). Dropping a card on a
// column sets that task's hidden status form and submits it via
// requestSubmit() (never .submit(), which would skip the form's own submit
// event and any CSP/validation wired to it), causing a normal
// POST/redirect/GET back to #pboard.
(function () {
  var board = document.getElementById('project-board');
  if (!board) { return; }

  var draggedId = null;

  board.querySelectorAll('.board-card[draggable="true"]').forEach(function (card) {
    card.addEventListener('dragstart', function (e) {
      draggedId = card.dataset.taskId;
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
      var form = document.getElementById('board-status-form-' + id);
      if (!form) { return; }
      var statusInput = form.querySelector('input[name="status"]');
      var newStatus = col.dataset.status;
      if (statusInput.value === newStatus) { return; } // dropped back in its own column
      statusInput.value = newStatus;
      form.requestSubmit();
    });
  });
})();
