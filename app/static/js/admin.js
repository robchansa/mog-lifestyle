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

  // On a phone the section list is a sideways-scrolling row; start it with
  // the current section in view rather than always at "Overview".
  var nav = document.querySelector(".admin-nav");
  var current = nav && nav.querySelector(".is-active");
  if (nav && current && nav.scrollWidth > nav.clientWidth) {
    nav.scrollLeft = current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2;
  }

  // Charts: hover already shows a bar's detail (CSS). On touch screens there
  // is no hover, so a tap pins the detail open; a tap elsewhere closes it.
  document.addEventListener("click", function (e) {
    var hit = e.target.closest(".chart__hit");
    document.querySelectorAll(".chart__hit.is-active").forEach(function (open) {
      if (open !== hit) open.classList.remove("is-active");
    });
    if (hit) hit.classList.toggle("is-active");
  });

  // Custom range: keep "to" from landing before "from", and vice versa.
  document.querySelectorAll("[data-range-form]").forEach(function (form) {
    var from = form.elements.from, to = form.elements.to;
    if (!from || !to) return;
    var sync = function () {
      if (from.value) to.min = from.value;
      if (to.value) from.max = to.value;
    };
    from.addEventListener("change", sync);
    to.addEventListener("change", sync);
    sync();
  });
})();
