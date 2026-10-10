/* One consent-gated Google tag for the shared MOG page layout. */
(function () {
  "use strict";

  var measurementId = "G-HVPD9NR41B";
  var storageKey = "mog_analytics_consent";
  var choice = null;
  try { choice = window.localStorage.getItem(storageKey); } catch (_) { /* storage disabled */ }

  function remember(value) {
    choice = value;
    try { window.localStorage.setItem(storageKey, value); } catch (_) { /* this page only */ }
  }

  function start() {
    if (window.mogGoogleTagStarted) return;
    window.mogGoogleTagStarted = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    // Each server-rendered route is a full document load. Strip query strings
    // so checkout tokens, searches and account details cannot enter page URLs.
    var referrer = "";
    if (document.referrer) {
      try {
        var previous = new URL(document.referrer);
        referrer = previous.origin +
          (previous.origin === window.location.origin ? previous.pathname : "/");
      } catch (_) { /* ignore malformed referrers */ }
    }
    window.gtag("config", measurementId, {
      page_location: window.location.origin + window.location.pathname,
      page_path: window.location.pathname,
      page_title: document.title,
      page_referrer: referrer
    });
    var script = document.createElement("script");
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + measurementId;
    document.head.appendChild(script);
  }

  function clearGoogleCookies() {
    var names = document.cookie.split(";").map(function (part) {
      return part.trim().split("=")[0];
    }).filter(function (name) { return /^_ga(?:_|$)/.test(name); });
    var domain = window.location.hostname;
    names.forEach(function (name) {
      ["", "; Domain=" + domain, "; Domain=." + domain].forEach(function (scope) {
        document.cookie = name + "=; Max-Age=0; Path=/; SameSite=Lax" + scope;
      });
    });
  }

  function ready() {
    var panel = document.querySelector("[data-analytics-consent]");
    if (!panel) return;
    var show = function () { panel.hidden = false; };
    var hide = function () { panel.hidden = true; };
    var accept = panel.querySelector("[data-analytics-accept]");
    var reject = panel.querySelector("[data-analytics-reject]");
    accept.addEventListener("click", function () {
      remember("yes");
      hide();
      start();
    });
    reject.addEventListener("click", function () {
      remember("no");
      hide();
      if (window.mogGoogleTagStarted) {
        if (window.gtag) window.gtag("consent", "update", { analytics_storage: "denied" });
        clearGoogleCookies();
        window.location.reload();
      }
    });
    document.querySelectorAll("[data-analytics-settings]").forEach(function (button) {
      button.addEventListener("click", show);
    });
    if (choice === "yes") start();
    else if (choice !== "no") show();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
}());
