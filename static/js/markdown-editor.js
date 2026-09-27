/*
 * Markdown description editor: GitHub-style Write | Preview tabs, live
 * character counter and formatting-help toggle for [data-md-editor] blocks
 * rendered by templates/submissions/partials/markdown_editor_box.html.
 *
 * Progressive enhancement: the tab strip and footer ship with `hidden`; this
 * script reveals them. Without JS the plain textarea keeps working.
 * Preview is fetched from the server's shared renderer whenever the text has
 * changed since the last successful render, so it is never stale.
 * WAI-ARIA tabs pattern (automatic activation).
 */
(function () {
  "use strict";

  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var f = document.querySelector("[name=csrfmiddlewaretoken]");
    return f ? f.value : "";
  }

  // Python's str.strip() whitespace set: JS \s minus U+FEFF (BOM), plus the
  // separators U+001C-U+001F and NEL U+0085. String.trim() differs on those.
  var PY_WS = "\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  var PY_STRIP = new RegExp("^[" + PY_WS + "]+|[" + PY_WS + "]+$", "g");

  // Same length the server validates: NFC, str.strip(), in code points.
  // Browsers submit textarea line breaks as CRLF, so each one counts as 2.
  function serverLength(s) {
    return Array.from(s.normalize("NFC").replace(PY_STRIP, "").replace(/\r?\n/g, "\r\n")).length;
  }

  // Error fragments (rate limit, too long, unavailable) must not be reused.
  function isErrorFragment(html) {
    var probe = document.createElement("template"); // inert: nothing loads or runs
    probe.innerHTML = html;
    return probe.content.querySelector(".md-editor__alert[role='alert']") !== null;
  }

  function init(root) {
    if (!root || root.dataset.mdReady === "1") return;
    var textarea = root.querySelector("[data-md-panel='write'] textarea");
    var tabs = Array.prototype.slice.call(root.querySelectorAll("[role='tab']"));
    var writePanel = root.querySelector("[data-md-panel='write']");
    var previewPanel = root.querySelector("[data-md-panel='preview']");
    var counter = root.querySelector("[data-md-counter]");
    var helpToggle = root.querySelector("[data-md-help-toggle]");
    var help = helpToggle && document.getElementById(helpToggle.getAttribute("aria-controls"));
    if (!textarea || tabs.length !== 2 || !writePanel || !previewPanel) return;
    root.dataset.mdReady = "1";

    root.querySelectorAll("[data-md-enhance]").forEach(function (el) { el.hidden = false; });
    if (counter) {
      var described = (textarea.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      if (described.indexOf(counter.id) === -1) described.push(counter.id);
      textarea.setAttribute("aria-describedby", described.join(" "));
    }

    var requestSeq = 0;
    var lastRenderedValue = null;
    var lastRenderedHtml = "";

    function renderPreview() {
      var value = textarea.value;
      if (value === lastRenderedValue) {
        requestSeq++; // discard any in-flight preview
        previewPanel.innerHTML = lastRenderedHtml;
        previewPanel.removeAttribute("aria-busy");
        return;
      }
      var seq = ++requestSeq;
      // Never leave an outdated render readable while the new one loads.
      previewPanel.innerHTML = '<p class="md-editor__empty">Loading preview…</p>';
      previewPanel.setAttribute("aria-busy", "true");
      var body = new FormData();
      body.append("service_description", value);
      body.append("csrfmiddlewaretoken", csrfToken());
      fetch(root.dataset.mdPreviewUrl, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" }
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.text();
        })
        .then(function (html) {
          if (seq !== requestSeq) return;
          previewPanel.innerHTML = html;
          if (isErrorFragment(html)) {
            lastRenderedValue = null;
          } else {
            lastRenderedValue = value;
            lastRenderedHtml = html;
          }
        })
        .catch(function () {
          if (seq !== requestSeq) return;
          lastRenderedValue = null;
          previewPanel.innerHTML = '<div class="md-editor__alert" role="alert">Preview unavailable. Please try again.</div>';
        })
        .then(function () {
          if (seq === requestSeq) previewPanel.removeAttribute("aria-busy");
        });
    }

    function select(tab, opts) {
      opts = opts || {};
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      if (tab.dataset.mdTab === "preview") {
        if (!writePanel.hidden) previewPanel.style.minHeight = writePanel.offsetHeight + "px";
        writePanel.hidden = true;
        previewPanel.hidden = false;
        renderPreview();
      } else {
        requestSeq++; // discard any in-flight preview
        previewPanel.removeAttribute("aria-busy");
        previewPanel.hidden = true;
        writePanel.hidden = false;
        if (!opts.keyboard) textarea.focus();
      }
      // Preview by mouse: Safari/Firefox on macOS do not focus a clicked
      // button, so focus would fall to <body> once the textarea is hidden.
      if (opts.keyboard || tab.dataset.mdTab === "preview") tab.focus();
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { select(tab); });
      tab.addEventListener("keydown", function (e) {
        // Leave modified keys (e.g. Alt+Arrow history navigation) to the browser.
        if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
        var j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
        if (j === undefined) return;
        e.preventDefault();
        select(tabs[(j + tabs.length) % tabs.length], { keyboard: true });
      });
    });

    // A browser validation bubble cannot point at a hidden textarea.
    textarea.addEventListener("invalid", function () {
      if (writePanel.hidden) select(tabs[0]);
    });

    function updateCount() {
      if (!counter) return;
      var n = serverLength(textarea.value);
      var min = parseInt(counter.dataset.min, 10);
      var max = parseInt(counter.dataset.max, 10);
      var text = n.toLocaleString("en") + " / " + max.toLocaleString("en");
      var tooShort = n > 0 && n < min;
      if (tooShort) text += " · at least " + min + " characters";
      counter.textContent = text;
      counter.classList.toggle("is-error", tooShort || n > max);
      counter.classList.toggle("is-warn", !tooShort && n <= max && n >= Math.ceil(max * 0.9));
    }
    textarea.addEventListener("input", updateCount);
    textarea.addEventListener("change", updateCount);
    updateCount();

    if (helpToggle && help) {
      helpToggle.addEventListener("click", function () {
        var open = helpToggle.getAttribute("aria-expanded") === "true";
        helpToggle.setAttribute("aria-expanded", open ? "false" : "true");
        help.hidden = open;
      });
    }
  }

  function initAll(scope) {
    var el = scope || document;
    if (el.matches && el.matches("[data-md-editor]")) init(el);
    if (el.querySelectorAll) el.querySelectorAll("[data-md-editor]").forEach(init);
  }

  window.MarkdownEditor = { init: init };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initAll(); });
  } else {
    initAll();
  }
  document.addEventListener("htmx:afterSettle", function (e) { initAll(e.target); });
})();
