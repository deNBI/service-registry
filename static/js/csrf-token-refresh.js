/*
 * csrf-token-refresh.js
 * =====================
 * Rewrites a form's hidden `csrfmiddlewaretoken` field from the live
 * `csrftoken` cookie immediately before the form is submitted.
 *
 * Why: the four native-POST forms (register, update, edit, deprecate) embed a
 * CSRF token at render time. That token can drift out of sync with the
 * browser's `csrftoken` cookie — e.g. a concurrent first-visit / multi-tab
 * race where a later response overwrote the cookie with a new secret, or
 * SameSite=Strict suppressing the cookie on external-link entry — which makes
 * Django reject the POST with a spurious 403 "CSRF token incorrect". Reading
 * the token from the cookie at submit time guarantees it always matches.
 *
 * Security: this is the canonical Django AJAX CSRF pattern. The cookie is only
 * readable from our own origin (Same-Origin Policy), so a cross-origin
 * attacker cannot obtain it; server-side validation is unchanged and forged
 * tokens are still rejected. The field is only overwritten when a cookie is
 * present, so the server-rendered token remains the fallback (no regression).
 * Mirrors the admin AJAX path (change_list.html) and base.html's HTMX header.
 */
(function () {
  "use strict";

  function csrfCookie() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (!form || typeof form.method !== "string") return;
      if (form.method.toLowerCase() !== "post") return;

      var field = form.querySelector('input[name="csrfmiddlewaretoken"]');
      if (!field) return;

      var token = csrfCookie();
      if (token) field.value = token;
    },
    true // capture phase: run before any other submit handler and the POST
  );
})();
