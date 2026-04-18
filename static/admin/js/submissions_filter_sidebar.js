/*
 * Enhanced filter sidebar behaviour for ServiceSubmissionAdmin changelist.
 *
 * Responsibilities:
 *   1. Boost filter/search/pagination/sort/date-hierarchy links to HTMX
 *      GET requests that swap #content-main without full page reload.
 *   2. Preserve page scroll, sidebar scroll, per-section scroll, open/closed
 *      <details> state, search-box values, and focus across swaps.
 *   3. Per-section client-side search (case-insensitive).
 *   4. Surface fetch errors in the sidebar's #changelist-filter-error banner.
 *
 * Scope: only active when `#changelist-filter.changelist-filter--enhanced`
 * exists on the page (i.e. ServiceSubmissionAdmin changelist).
 */
(function () {
  "use strict";

  var SIDEBAR_SELECTOR = "#changelist-filter.changelist-filter--enhanced";
  var STORAGE_PREFIX = "svcsub:filter:";
  var state = {
    pageScroll: 0,
    sidebarScroll: 0,
    sectionScrolls: {},
    focusId: null,
    searchValues: {},
    openSections: {},
  };

  function qs(root, sel) { return (root || document).querySelector(sel); }
  function qsa(root, sel) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function storageKey(field, suffix) { return STORAGE_PREFIX + field + ":" + suffix; }

  function readSessionFlag(field, suffix) {
    try { return sessionStorage.getItem(storageKey(field, suffix)); }
    catch (e) { return null; }
  }
  function writeSessionFlag(field, suffix, value) {
    try { sessionStorage.setItem(storageKey(field, suffix), value); }
    catch (e) { /* quota or disabled */ }
  }

  /* ── State capture / restore ─────────────────────────────────────────── */

  function captureState() {
    state.pageScroll = window.scrollY || window.pageYOffset || 0;
    var sidebar = qs(document, SIDEBAR_SELECTOR);
    state.sidebarScroll = sidebar ? sidebar.scrollTop : 0;
    state.sectionScrolls = {};
    qsa(document, SIDEBAR_SELECTOR + " details.filter-section").forEach(function (det) {
      var field = det.getAttribute("data-field");
      var opts = qs(det, ".filter-options");
      if (field && opts) state.sectionScrolls[field] = opts.scrollTop;
    });
    state.focusId = document.activeElement ? document.activeElement.id : null;
    state.searchValues = {};
    qsa(document, SIDEBAR_SELECTOR + " details.filter-section").forEach(function (det) {
      var field = det.getAttribute("data-field");
      var input = qs(det, ".filter-search");
      if (field && input) state.searchValues[field] = input.value;
    });
  }

  function restoreState() {
    // Restore page scroll. `behavior: "instant"` is ignored in older browsers
    // and some of them treat a missing `behavior` as smooth when CSS sets
    // `scroll-behavior: smooth` — the 2-arg form guarantees a jump.
    try {
      window.scrollTo({ top: state.pageScroll, left: 0, behavior: "instant" });
    } catch (e) {
      window.scrollTo(0, state.pageScroll);
    }

    var sidebar = qs(document, SIDEBAR_SELECTOR);
    if (sidebar) sidebar.scrollTop = state.sidebarScroll || 0;

    qsa(document, SIDEBAR_SELECTOR + " details.filter-section").forEach(function (det) {
      var field = det.getAttribute("data-field");
      if (!field) return;

      // Restore open/closed (sessionStorage is the durable source; fall back
      // to the server-rendered open attribute).
      var stored = readSessionFlag(field, "open");
      if (stored === "1") det.setAttribute("open", "");
      else if (stored === "0") det.removeAttribute("open");

      // Restore per-section search value and re-apply filtering.
      var input = qs(det, ".filter-search");
      if (input) {
        var storedQ = readSessionFlag(field, "q");
        var value = state.searchValues[field];
        if (value === undefined || value === null) {
          value = storedQ || "";
        }
        input.value = value;
        applySectionSearch(det, value);
      }

      // Restore scroll after filtering so scrollTop is valid.
      var opts = qs(det, ".filter-options");
      if (opts && state.sectionScrolls[field] !== undefined) {
        opts.scrollTop = state.sectionScrolls[field];
      }
    });

    // Restore focus if the target still exists.
    if (state.focusId) {
      var target = document.getElementById(state.focusId);
      if (target && typeof target.focus === "function") {
        try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); }
      }
    }
  }

  /* ── Per-section search ──────────────────────────────────────────────── */

  function applySectionSearch(section, rawQuery) {
    var q = (rawQuery || "").trim().toLowerCase();
    var options = qsa(section, "ul.filter-options > li.filter-option");
    var visible = 0;
    options.forEach(function (li) {
      var label = li.getAttribute("data-label") || "";
      var match = q === "" || label.indexOf(q) !== -1;
      if (match) {
        li.hidden = false;
        visible++;
      } else {
        li.hidden = true;
      }
    });
    var noMatch = qs(section, "li.filter-no-match");
    if (noMatch) noMatch.hidden = visible > 0;

    var live = qs(section, ".filter-results-live");
    if (live) {
      if (q === "") {
        live.textContent = "";
      } else {
        live.textContent = visible + " of " + options.length + " options";
      }
    }
  }

  function bindSearchInputs() {
    qsa(document, SIDEBAR_SELECTOR + " details.filter-section").forEach(function (det) {
      var input = qs(det, ".filter-search");
      if (!input || input.dataset.bound === "1") return;
      input.dataset.bound = "1";
      input.addEventListener("input", function () {
        applySectionSearch(det, input.value);
        var field = det.getAttribute("data-field");
        if (field) writeSessionFlag(field, "q", input.value);
      });
    });
  }

  function bindDetailsToggle() {
    qsa(document, SIDEBAR_SELECTOR + " details.filter-section").forEach(function (det) {
      if (det.dataset.toggleBound === "1") return;
      det.dataset.toggleBound = "1";
      det.addEventListener("toggle", function () {
        var field = det.getAttribute("data-field");
        if (!field) return;
        writeSessionFlag(field, "open", det.open ? "1" : "0");
      });
    });
  }

  /* ── HTMX wiring ─────────────────────────────────────────────────────── */

  function htmxAvailable() { return typeof window.htmx !== "undefined"; }

  function boostNode(el) {
    if (el.dataset.boosted === "1") return;
    var href;
    if (el.tagName === "FORM") {
      if ((el.method || "get").toLowerCase() !== "get") return;
      href = el.getAttribute("action") || window.location.pathname;
      el.setAttribute("hx-get", href);
      el.setAttribute("hx-trigger", "submit");
    } else {
      href = el.getAttribute("href");
      if (!href) return;
      // Skip external links and anchors-only.
      if (href.charAt(0) === "#" && href.length > 1) return;
      el.setAttribute("hx-get", href);
    }
    el.setAttribute("hx-target", "#content-main");
    el.setAttribute("hx-select", "#content-main");
    el.setAttribute("hx-swap", "outerHTML scroll:false show:no");
    el.setAttribute("hx-push-url", "true");
    el.setAttribute("hx-sync", "closest body:replace");
    el.dataset.boosted = "1";
  }

  function boostAll() {
    if (!htmxAvailable()) return;
    var selectors = [
      SIDEBAR_SELECTOR + " a.pill-remove",
      SIDEBAR_SELECTOR + " a.pill-clear",
      SIDEBAR_SELECTOR + " ul.filter-options > li.filter-option > a",
      SIDEBAR_SELECTOR + " #changelist-filter-extra-actions a",
      "#toolbar #changelist-search",
      ".xfull a",
      "#date-hierarchy a",
      ".paginator a",
      "#result_list thead th.sortable a",
    ];
    qsa(document, selectors.join(", ")).forEach(boostNode);
    window.htmx.process(document.body);
  }

  /* ── Error banner ────────────────────────────────────────────────────── */

  function showError(msg) {
    var banner = qs(document, "#changelist-filter-error");
    if (!banner) return;
    banner.textContent = msg;
    banner.hidden = false;
  }
  function hideError() {
    var banner = qs(document, "#changelist-filter-error");
    if (banner) { banner.hidden = true; banner.textContent = ""; }
  }

  /* ── Init ────────────────────────────────────────────────────────────── */

  function init() {
    if (!qs(document, SIDEBAR_SELECTOR)) return;
    if (htmxAvailable()) {
      try { window.htmx.config.historyCacheSize = 0; } catch (e) { /* noop */ }
    }
    bindSearchInputs();
    bindDetailsToggle();
    boostAll();

    // Re-apply any persisted search values on initial load so the sidebar
    // reflects the user's previous session state.
    qsa(document, SIDEBAR_SELECTOR + " details.filter-section").forEach(function (det) {
      var field = det.getAttribute("data-field");
      var input = qs(det, ".filter-search");
      if (!field || !input) return;
      var storedQ = readSessionFlag(field, "q");
      var storedOpen = readSessionFlag(field, "open");
      if (storedQ) {
        input.value = storedQ;
        applySectionSearch(det, storedQ);
      }
      if (storedOpen === "1") det.setAttribute("open", "");
      else if (storedOpen === "0") det.removeAttribute("open");
    });
  }

  function onDomReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  onDomReady(function () {
    init();
    if (!htmxAvailable()) return;

    document.body.addEventListener("htmx:configRequest", function (evt) {
      if (!isOurSwap(evt)) return;
      hideError();
      captureState();
    });

    document.body.addEventListener("htmx:afterSwap", function (evt) {
      if (!isSwapOfContentMain(evt)) return;
      bindSearchInputs();
      bindDetailsToggle();
      boostAll();
      restoreState();
    });

    document.body.addEventListener("htmx:responseError", function (evt) {
      if (!isOurSwap(evt)) return;
      var status = evt.detail && evt.detail.xhr ? evt.detail.xhr.status : "?";
      showError("Server error (" + status + "). Please reload and try again.");
    });

    document.body.addEventListener("htmx:sendError", function (evt) {
      if (!isOurSwap(evt)) return;
      showError("Network error. Please check your connection and try again.");
    });
  });

  function isOurSwap(evt) {
    var elt = evt.target || (evt.detail && evt.detail.elt);
    if (!elt || !elt.getAttribute) return false;
    var target = elt.getAttribute("hx-target");
    return target === "#content-main" || (elt.closest && elt.closest("[hx-target='#content-main']"));
  }

  function isSwapOfContentMain(evt) {
    var target = evt.detail && evt.detail.target;
    if (!target) return false;
    return target.id === "content-main";
  }
})();
