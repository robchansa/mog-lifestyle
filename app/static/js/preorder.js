/* MOG Lifestyle — the pre-launch countdown page (app/views_preorder.py).
   Progressive enhancement only. Without JavaScript the page shows the time
   left at load plus the opening date, Notify Me posts normally, and once the
   time has passed any reload shows the full site. With it:
   - the "Preorders Open In" countdown ticks; at zero it becomes "Preorders
     Are Open" and takes the visitor into the site a moment later;
   - the email is checked before sending, and the form submits in place with
     the result announced to screen readers. */
(function () {
  "use strict";

  var REVEAL_DELAY_MS = 2500;

  /* ------------------------------------------------------- countdown */
  var page = document.querySelector("[data-preorder][data-opens-at]");
  if (page) startCountdown(page);

  function startCountdown(page) {
    var opensAt = Date.parse(page.getAttribute("data-opens-at"));
    if (isNaN(opensAt)) return;

    // Count against the server's clock, not the device's: a phone set ten
    // minutes fast must not show the doors open early. Sub-second gaps are
    // just network latency and are ignored.
    var serverNow = Date.parse(page.getAttribute("data-server-now"));
    var skew = isNaN(serverNow) ? 0 : serverNow - Date.now();
    if (Math.abs(skew) < 2000) skew = 0;
    var now = function () { return Date.now() + skew; };

    var title = page.querySelector("[data-preorder-title]");
    var countdownPanel = page.querySelector("[data-countdown-panel]");
    var openPanel = page.querySelector("[data-open-panel]");
    var prefix = page.querySelector("[data-when-prefix]");
    var announce = page.querySelector("[data-countdown-announce]");
    var revealUrl = page.getAttribute("data-reveal-url") || "/";
    var units = {};
    page.querySelectorAll("[data-unit]").forEach(function (el) {
      units[el.getAttribute("data-unit")] = el;
    });

    showLocalTime(page, opensAt);

    var timer = null;
    var opened = openPanel && !openPanel.hidden;

    function write(el, value) {
      if (el && el.textContent !== value) el.textContent = value;
    }
    function pad(n) { return n < 10 ? "0" + n : String(n); }

    function typingSomething() {
      var el = document.activeElement;
      return el && el.tagName === "INPUT" && el.value;
    }

    function reveal() {
      // Never yank the page away from someone mid-way through typing their
      // email; the "Enter the site" button is right there for them.
      if (typingSomething()) return;
      window.location.assign(revealUrl);
    }

    function openUp() {
      if (opened) return;
      opened = true;
      if (title) title.textContent = "Preorders Are Open";
      if (countdownPanel) countdownPanel.hidden = true;
      if (openPanel) openPanel.hidden = false;
      if (prefix) prefix.textContent = "Opened";
      if (announce) announce.textContent = "Preorders are open. Taking you to the site.";
      setTimeout(reveal, REVEAL_DELAY_MS);
    }

    function tick() {
      clearTimeout(timer);
      var msLeft = opensAt - now();
      if (msLeft <= 0) { openUp(); return; }
      // Rounded up, so the last second reads 00 00 00 01 rather than zeros.
      var left = Math.ceil(msLeft / 1000);
      write(units.days, String(Math.floor(left / 86400)));
      write(units.hours, pad(Math.floor((left % 86400) / 3600)));
      write(units.minutes, pad(Math.floor((left % 3600) / 60)));
      write(units.seconds, pad(left % 60));
      // Wake exactly when the displayed second changes, not on a drifting
      // one-second interval.
      timer = setTimeout(tick, (msLeft % 1000 || 1000) + 15);
    }

    if (opened) setTimeout(reveal, REVEAL_DELAY_MS);
    else tick();
    // Background tabs throttle timers; catch up the moment the page is seen.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !opened) tick();
    });
  }

  function showLocalTime(page, opensAt) {
    var el = page.querySelector("[data-local-time]");
    var storeOffset = parseInt(page.getAttribute("data-zone-offset"), 10);
    if (!el || isNaN(storeOffset)) return;
    var when = new Date(opensAt);
    if (-when.getTimezoneOffset() === storeOffset) return;   // same zone as the store
    try {
      el.textContent = "Your time: " + when.toLocaleString(undefined, {
        weekday: "long", month: "long", day: "numeric",
        hour: "numeric", minute: "2-digit", timeZoneName: "short"
      }) + ".";
      el.hidden = false;
    } catch (err) { /* very old browser: the store-time line still stands */ }
  }

  /* -------------------------------------------------------- Notify Me */
  var EMAIL_RE = /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/;
  var CHECK_ICON = '<svg class="icon" width="18" height="18" viewBox="0 0 24 24" ' +
    'fill="currentColor" aria-hidden="true" focusable="false">' +
    '<path d="M9.6 16.2 5.4 12l-1.4 1.4 5.6 5.6L20.4 8.2 19 6.8 9.6 16.2Z"/></svg>';

  // Messages come from the server-rendered markup, so they always match the
  // server's own wording.
  function problem(input) {
    var value = input.value.trim();
    if (!value) return input.getAttribute("data-msg-required") || "This field is required.";
    if (input.type === "email" && (value.length > 254 || !EMAIL_RE.test(value))) {
      return input.getAttribute("data-msg-format");
    }
    return "";
  }

  function setError(input, message) {
    var field = input.closest(".field");
    var slot = field ? field.querySelector("[data-error-for]") : null;
    if (!slot) return;
    var ids = (input.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter(function (id) { return id && id !== slot.id; });
    if (message) {
      slot.textContent = message;
      slot.hidden = false;
      field.classList.add("field--error");
      input.setAttribute("aria-invalid", "true");
      ids.push(slot.id);
    } else {
      slot.textContent = "";
      slot.hidden = true;
      field.classList.remove("field--error");
      input.removeAttribute("aria-invalid");
    }
    if (ids.length) input.setAttribute("aria-describedby", ids.join(" "));
    else input.removeAttribute("aria-describedby");
  }

  document.querySelectorAll("[data-preorder-form]").forEach(function (form) {
    // Our own messages replace the browser's bubbles, which are not read out
    // consistently and cannot be styled.
    form.setAttribute("novalidate", "");
    var inputs = Array.prototype.slice.call(form.querySelectorAll("input[required]"));
    var summary = form.querySelector("[data-form-error]");
    var status = form.parentElement.querySelector("[data-form-status]");
    var submit = form.querySelector("button[type=submit]");

    function showSummary(message) {
      if (!summary) return;
      summary.textContent = message || "";
      summary.hidden = !message;
    }

    inputs.forEach(function (input) {
      // Judge a field once the visitor leaves it with something typed, then
      // re-check as they type so an error clears the moment it is fixed.
      input.addEventListener("blur", function () {
        if (input.value || input.getAttribute("aria-invalid")) setError(input, problem(input));
      });
      input.addEventListener("input", function () {
        if (input.getAttribute("aria-invalid")) setError(input, problem(input));
      });
    });

    form.addEventListener("submit", function (event) {
      var first = null;
      inputs.forEach(function (input) {
        var message = problem(input);
        setError(input, message);
        if (message && !first) first = input;
      });
      showSummary("");
      if (status) status.innerHTML = "";
      if (first) {
        event.preventDefault();
        first.focus();
        return;
      }
      if (!window.fetch) return;            // the plain form post still works
      event.preventDefault();
      send();
    });

    function send() {
      var label = submit ? submit.textContent : "";
      if (submit) {
        submit.classList.add("is-busy");
        submit.setAttribute("aria-disabled", "true");
        submit.textContent = "Sending…";
      }
      var done = function () {
        if (submit) {
          submit.classList.remove("is-busy");
          submit.removeAttribute("aria-disabled");
          submit.textContent = label;
        }
      };
      fetch(form.action, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "Accept": "application/json"
        },
        body: new URLSearchParams(new FormData(form)).toString(),
        credentials: "same-origin"
      }).then(function (response) {
        return response.json().catch(function () { return null; });
      }).then(function (data) {
        done();
        if (data && data.ok) return succeed(data);
        fail(data);
      }).catch(function () {
        done();
        showSummary("We couldn’t reach the store. Check your connection and try again.");
      });
    }

    function succeed(data) {
      if (data.meta_event_id && typeof window.CustomEvent === "function") {
        document.dispatchEvent(new CustomEvent("mog:preorder-signup", {
          detail: { eventId: data.meta_event_id }
        }));
      }
      form.reset();
      inputs.forEach(function (input) { setError(input, ""); });
      if (!status) return;
      var message = document.createElement("p");
      message.className = "form-status__message";
      message.innerHTML = CHECK_ICON + "<span></span>";
      message.lastChild.textContent = data.message || "Thank you.";
      status.appendChild(message);
      if (data.note) {
        var note = document.createElement("p");
        note.className = "form-status__note";
        note.textContent = data.note;
        status.appendChild(note);
      }
    }

    function fail(data) {
      var errors = (data && data.errors) || {};
      var first = null;
      inputs.forEach(function (input) {
        var message = errors[input.name] || "";
        setError(input, message);
        if (message && !first) first = input;
      });
      var general = (data && (data.error || errors._)) || "";
      if (!first && !general) general = "Something went wrong. Please try again.";
      showSummary(general);
      if (first) first.focus();
    }
  });
})();
