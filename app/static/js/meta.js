/* Opt-in Meta measurement for the public preorder homepage only. */
(function () {
  "use strict";
  function ready() {
  var pixel = "1439220394903801";
  var key = "mog_meta_consent_v1";
  var panel = document.querySelector("[data-meta-consent]");
  if (!panel) return;
  var location = window.location;
  var blocked = navigator.globalPrivacyControl === true || navigator.doNotTrack === "1";
  var choice = null, started = false, seen = {};
  try { choice = window.localStorage.getItem(key); } catch (_) { /* session only */ }

  function remember(value) {
    choice = value;
    try { window.localStorage.setItem(key, value); } catch (_) { /* session only */ }
  }
  function safePage() {
    if (!/^(www\.)?moglifestyle\.fit$/.test(location.hostname) || location.pathname !== "/") return false;
    var allowed = /^(utm_source|utm_medium|utm_campaign|utm_content|utm_term|fbclid|ad_settings|view)$/;
    var safe = true;
    new URLSearchParams(location.search).forEach(function (_, name) {
      if (!allowed.test(name)) safe = false;
    });
    // Meta reads document.referrer itself. Do not load it when a previous
    // same-site private page, or a query-bearing referrer, could leak details.
    if (document.referrer) {
      try {
        var previous = new URL(document.referrer);
        if (previous.search || (previous.origin === location.origin &&
            !/^(\/|\/privacy|\/about)$/.test(previous.pathname))) safe = false;
      } catch (_) { safe = false; }
    }
    return safe;
  }
  function start() {
    if (started || choice !== "yes" || blocked || !safePage()) return;
    started = true;
    var fbq = window.fbq = function () {
      if (fbq.callMethod) fbq.callMethod.apply(fbq, arguments);
      else fbq.queue.push(arguments);
    };
    fbq.push = fbq; fbq.loaded = true; fbq.version = "2.0"; fbq.queue = [];
    window._fbq = fbq;
    fbq("consent", "grant");
    fbq("set", "autoConfig", false, pixel);
    fbq("init", pixel); // No advanced matching or customer identifiers.
    fbq("track", "PageView");
    var script = document.createElement("script");
    script.async = true;
    script.src = "https://connect.facebook.net/en_US/fbevents.js";
    document.head.appendChild(script);
  }
  function clearCookies() {
    ["_fbp", "_fbc"].forEach(function (name) {
      ["", "; Domain=" + location.hostname, "; Domain=." + location.hostname,
       "; Domain=.moglifestyle.fit"].forEach(function (domain) {
        document.cookie = name + "=; Max-Age=0; Path=/; SameSite=Lax" + domain;
      });
    });
  }
  var analytics = document.querySelector("[data-analytics-consent]");
  var requested = new URLSearchParams(location.search).get("ad_settings") === "1";
  function showWhenReady() {
    if (analytics && !analytics.hidden) { panel.hidden = true; return; }
    if ((requested || (!blocked && choice !== "yes" && choice !== "no")) &&
        (!analytics || analytics.hidden)) panel.hidden = false;
  }
  var accept = panel.querySelector("[data-meta-accept]");
  accept.disabled = blocked;
  if (blocked) panel.querySelector("p").textContent =
    "Your browser's privacy signal disables Meta ad measurement. No Meta tracking will load.";
  accept.addEventListener("click", function () {
    remember("yes"); requested = false; panel.hidden = true; start();
  });
  panel.querySelector("[data-meta-reject]").addEventListener("click", function () {
    remember("no"); requested = false; panel.hidden = true;
    if (started && window.fbq) window.fbq("consent", "revoke");
    clearCookies();
    if (started) window.location.reload();
  });
  if (analytics) new MutationObserver(showWhenReady).observe(analytics, {attributes: true, attributeFilter: ["hidden"]});
  document.addEventListener("mog:preorder-signup", function (event) {
    var id = event.detail && event.detail.eventId;
    if (!started || choice !== "yes" || blocked || typeof id !== "string" ||
        !/^[a-f0-9]{32}$/.test(id) || seen[id]) return;
    seen[id] = true;
    window.fbq("track", "Lead", {content_name: "Preorder notification signup"}, {eventID: id});
  });
  if (blocked) { clearCookies(); }
  else start();
  showWhenReady();
  }
  // Google registers its consent UI first. Wait for that UI to initialize
  // before deciding whether the separate Meta prompt can be shown.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
}());
