/* MOG Lifestyle — progressive enhancement.
   Every feature here has a working no-JS fallback: forms submit normally,
   links navigate, filters are plain GET requests. */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function on(root, event, selector, handler) {
    root.addEventListener(event, function (e) {
      var match = e.target.closest(selector);
      if (match && root.contains(match)) handler(e, match);
    });
  }

  /* ---------------------------------------------------------- header */
  var header = document.querySelector("[data-header]");
  var heroEl = document.querySelector("[data-hero]");
  if (header) {
    var publishHeaderHeight = function () {
      document.documentElement.style.setProperty(
        "--header-height", header.offsetHeight + "px");
    };
    var setStuck = function () {
      header.classList.toggle("is-stuck", window.scrollY > 8);
      if (heroEl) {
        // Invert the bar while it sits over the photograph.
        var limit = heroEl.offsetHeight - header.offsetHeight;
        header.classList.toggle("is-over-hero", window.scrollY < limit);
      }
    };
    publishHeaderHeight();
    setStuck();
    window.addEventListener("scroll", setStuck, { passive: true });
    window.addEventListener("resize", function () {
      publishHeaderHeight();
      setStuck();
    }, { passive: true });
  }

  var navToggle = document.querySelector("[data-nav-toggle]");
  var nav = document.getElementById("site-nav");
  if (navToggle && nav) {
    navToggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", String(open));
      navToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      document.body.classList.toggle("is-locked", open);
      if (open) {
        var first = nav.querySelector("a");
        if (first) first.focus();
      }
    });
    nav.addEventListener("click", function (e) {
      if (e.target.closest("a")) {
        nav.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
        document.body.classList.remove("is-locked");
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("is-open")) {
        navToggle.click();
        navToggle.focus();
      }
    });
  }

  /* ----------------------------------------------------------- flash */
  var flash = document.querySelector("[data-flash]");
  if (flash) {
    document.cookie = "flash=; Path=/; Max-Age=0; SameSite=Lax";
    var dismiss = function () {
      flash.style.opacity = "0";
      flash.style.transform = "translate(-50%, 0.75rem)";
      setTimeout(function () { flash.remove(); }, 320);
    };
    var closer = flash.querySelector("[data-flash-close]");
    if (closer) closer.addEventListener("click", dismiss);
    setTimeout(dismiss, 5200);
  }

  /* --------------------------------------------------- scroll-driven hero */
  var hero = document.querySelector("[data-hero]");
  if (hero && !reduceMotion) {
    var ticking = false;
    var writeHero = function () {
      var height = hero.offsetHeight || 1;
      var scrolled = Math.max(0, window.scrollY);
      // The hero is pinned, so the page slides up over it. Progress runs
      // 0 -> 1 across exactly one screen of scrolling.
      var progress = Math.min(1, scrolled / height);
      hero.style.setProperty("--hero-progress", progress.toFixed(4));
      ticking = false;
    };
    var onHeroScroll = function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(writeHero);
    };
    writeHero();
    window.addEventListener("scroll", onHeroScroll, { passive: true });
    window.addEventListener("resize", onHeroScroll, { passive: true });
  }

  /* ------------------------------------------------------- category menus */
  var navItems = document.querySelectorAll("[data-nav-item]");
  var closeMenus = function (except) {
    navItems.forEach(function (item) {
      if (item === except) return;
      item.querySelector("[data-nav-menu]").classList.remove("is-open");
      item.querySelector("[data-nav-trigger]").setAttribute("aria-expanded", "false");
    });
  };
  navItems.forEach(function (item) {
    var trigger = item.querySelector("[data-nav-trigger]");
    var menu = item.querySelector("[data-nav-menu]");
    if (!trigger || !menu) return;

    trigger.addEventListener("click", function (e) {
      // On a wide screen the link still navigates to the department; on a
      // narrow one, or when the panel is shut, the first tap opens it.
      if (window.matchMedia("(min-width: 981px)").matches) return;
      var open = menu.classList.contains("is-open");
      closeMenus(item);
      if (!open) {
        e.preventDefault();
        menu.classList.add("is-open");
        trigger.setAttribute("aria-expanded", "true");
      }
    });
    item.addEventListener("mouseenter", function () {
      if (!window.matchMedia("(min-width: 981px)").matches) return;
      trigger.setAttribute("aria-expanded", "true");
    });
    item.addEventListener("mouseleave", function () {
      trigger.setAttribute("aria-expanded", "false");
    });
    item.addEventListener("focusin", function () {
      trigger.setAttribute("aria-expanded", "true");
    });
    item.addEventListener("focusout", function (e) {
      if (!item.contains(e.relatedTarget)) {
        trigger.setAttribute("aria-expanded", "false");
        menu.classList.remove("is-open");
      }
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeMenus(null);
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-nav-item]")) closeMenus(null);
  });

  /* ------------------------------------------------------- reveal-in */
  var reveals = document.querySelectorAll(".reveal");
  if (reveals.length && "IntersectionObserver" in window && !reduceMotion) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        io.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
    reveals.forEach(function (el) { io.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("is-in"); });
  }

  /* ------------------------------------------------------ shop filters */
  var filtersToggle = document.querySelector("[data-filters-toggle]");
  var filters = document.querySelector("[data-filters]");
  if (filtersToggle && filters) {
    filtersToggle.addEventListener("click", function () {
      var open = filters.classList.toggle("is-open");
      filtersToggle.setAttribute("aria-expanded", String(open));
      filtersToggle.textContent = open ? "Hide filters" : "Filters";
    });
  }
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () {
      var form = el.closest("form");
      if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
    });
  });

  /* --------------------------------------------------------- quantity */
  on(document, "click", "[data-qty]", function (e, button) {
    e.preventDefault();
    var wrap = button.closest(".qty");
    var input = wrap.querySelector("input");
    var step = button.dataset.qty === "up" ? 1 : -1;
    var max = parseInt(input.max || "99", 10);
    var next = Math.min(max, Math.max(1, parseInt(input.value || "1", 10) + step));
    if (String(next) === input.value) return;
    input.value = next;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  /* Auto-save cart quantity changes without a page reload. */
  on(document, "change", "[data-cart-qty]", function (e, input) {
    var form = input.closest("form");
    if (!form) return;
    var row = input.closest(".line-item");
    if (row) row.style.opacity = ".5";
    postForm(form).then(function (data) {
      if (data && data.ok) { window.location.reload(); }
      else { form.submit(); }
    }).catch(function () { form.submit(); });
  });

  /* ------------------------------------------------- add-to-cart (async) */
  on(document, "submit", "[data-add-to-cart]", function (e, form) {
    if (!window.fetch) return;
    e.preventDefault();
    var button = form.querySelector("button[type=submit]");
    var original = button ? button.textContent : "";
    if (button) { button.classList.add("is-busy"); button.textContent = "Adding…"; }
    postForm(form).then(function (data) {
      if (!data || !data.ok) throw new Error((data && data.error) || "Could not add");
      updateCartCount(data.cart_count);
      if (button) { button.textContent = "Added"; }
      toast(data.message || "Added to bag");
      setTimeout(function () {
        if (button) { button.classList.remove("is-busy"); button.textContent = original; }
      }, 1400);
    }).catch(function (err) {
      if (button) { button.classList.remove("is-busy"); button.textContent = original; }
      toast(err.message || "Something went wrong", "error");
    });
  });

  /* ------------------------------------------------------ async forms */
  on(document, "submit", "[data-async-form]", function (e, form) {
    if (!window.fetch) return;
    e.preventDefault();
    var status = form.parentElement.querySelector("[data-form-status]");
    postForm(form).then(function (data) {
      if (status) status.textContent = (data && data.message) || "Thank you.";
      if (data && data.ok) form.reset();
    }).catch(function () {
      if (status) status.textContent = "Something went wrong. Try again.";
    });
  });

  /* ------------------------------------------------- variant selection */
  var variantForm = document.querySelector("[data-variant-form]");
  if (variantForm) {
    var priceEl = document.querySelector("[data-variant-price]");
    var noteEl = document.querySelector("[data-variant-note]");
    var submit = variantForm.querySelector("button[type=submit]");
    var sync = function () {
      var checked = variantForm.querySelector("input[name=variant_id]:checked");
      if (!checked) return;
      if (priceEl && checked.dataset.price) priceEl.textContent = checked.dataset.price;
      var stock = parseInt(checked.dataset.stock || "0", 10);
      if (noteEl) {
        noteEl.textContent = stock === 0
          ? "Sold out in this size."
          : stock <= 6 ? ("Only " + stock + " left in this size.") : "In stock — ships in 1–2 business days.";
      }
      if (submit) {
        submit.disabled = stock === 0;
        submit.textContent = stock === 0 ? "Sold out" : "Add to bag";
      }
      var qtyInput = variantForm.querySelector("input[name=quantity]");
      if (qtyInput) qtyInput.max = String(Math.max(1, stock));
    };
    variantForm.addEventListener("change", sync);
    sync();
  }

  /* -------------------------------------------------------- utilities */
  function postForm(form) {
    var body = new URLSearchParams(new FormData(form)).toString();
    return fetch(form.action, {
      method: (form.method || "post").toUpperCase(),
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
      },
      body: body,
      credentials: "same-origin"
    }).then(function (r) { return r.json().catch(function () { return null; }); });
  }

  function updateCartCount(count) {
    document.querySelectorAll("[data-cart-count]").forEach(function (el) {
      el.textContent = count;
      el.classList.toggle("is-empty", !count);
    });
    var link = document.querySelector("[data-cart-link]");
    if (link) {
      link.setAttribute("aria-label", "Cart, " + count + (count === 1 ? " item" : " items"));
    }
  }

  function toast(message, tone) {
    var existing = document.querySelector("[data-flash]");
    if (existing) existing.remove();
    var el = document.createElement("div");
    el.className = "flash flash--" + (tone || "ok");
    el.setAttribute("role", "status");
    el.setAttribute("data-flash", "");
    el.innerHTML = "<span></span>";
    el.firstChild.textContent = message;
    document.body.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 320);
    }, 3200);
  }

  window.MOG = { toast: toast, updateCartCount: updateCartCount };
})();
