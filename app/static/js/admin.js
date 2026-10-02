/* Admin-only enhancements: confirmations, bulk select, and the tiny
   client-side table filter. */
(function () {
  "use strict";

  document.addEventListener("submit", function (e) {
    var form = e.target.closest("[data-confirm]");
    if (!form) return;
    if (!window.confirm(form.dataset.confirm)) e.preventDefault();
  });

  var filterInput = document.querySelector("[data-table-filter]");
  if (filterInput) {
    var target = document.querySelector(filterInput.dataset.tableFilter);
    filterInput.addEventListener("input", function () {
      var needle = filterInput.value.trim().toLowerCase();
      if (!target) return;
      target.querySelectorAll("tbody tr").forEach(function (row) {
        row.hidden = needle !== "" && row.textContent.toLowerCase().indexOf(needle) === -1;
      });
    });
  }

  document.querySelectorAll("[data-print]").forEach(function (btn) {
    btn.addEventListener("click", function () { window.print(); });
  });
})();
