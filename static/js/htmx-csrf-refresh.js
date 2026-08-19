/*
 * Keep htmx requests' CSRF token in sync with the live csrftoken cookie.
 *
 * htmx fixes the token at render time: it either uses hx-headers or serialises
 * the form's hidden csrfmiddlewaretoken. If the cookie rotates afterwards
 * (multi-tab / concurrent first visit), those go stale and Django rejects the
 * POST with a spurious 403 — e.g. inline validation at /register/validate/.
 *
 * htmx:configRequest fires after the request is built, so we overwrite the
 * token from the cookie there. Django reads the POSTed csrfmiddlewaretoken
 * before the header, so we refresh both. The htmx twin of csrf-token-refresh.js.
 */
(function () {
  "use strict";

  function csrfCookie() {
    // Anchored so a decoy cookie like `xcsrftoken` can't shadow the real one.
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  document.addEventListener("htmx:configRequest", function (event) {
    var token = csrfCookie();
    if (!token) return;
    event.detail.headers["X-CSRFToken"] = token;

    // Only rewrite the field htmx already sent, so we don't touch other requests.
    var params = event.detail.parameters;
    if (!params) return;
    if (typeof params.set === "function") {
      if (params.has("csrfmiddlewaretoken")) params.set("csrfmiddlewaretoken", token);
    } else if (Object.prototype.hasOwnProperty.call(params, "csrfmiddlewaretoken")) {
      params.csrfmiddlewaretoken = token;
    }
  });
})();
