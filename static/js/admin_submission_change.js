"use strict";
/**
 * admin_submission_change.js
 * Enhancements for the ServiceSubmission admin change view:
 *
 *  1. Scroll to the first field with a validation error and expand its
 *     fieldset if it is collapsed — so errors are never invisible.
 *
 *  2. Auto-expand the "Last Change Summary" fieldset when it contains
 *     actual change data (i.e. is not the placeholder "No change history"
 *     message), making it immediately visible for review.
 */
(function () {
  function init() {
    scrollToFirstError();
    autoExpandLastChangeSummary();
  }

  /**
   * Find the first .errorlist inside the form, expand its containing
   * fieldset (if collapsed), and scroll smoothly to it.
   */
  function scrollToFirstError() {
    var firstError = document.querySelector(".errorlist");
    if (!firstError) return;

    // Walk up and expand any collapsed fieldset ancestor.
    var node = firstError;
    while (node && node !== document.body) {
      if (node.tagName === "FIELDSET") {
        node.classList.remove("collapsed");
        // Django admin adds aria-hidden to collapsed content — remove it.
        var content = node.querySelector(".form-row, .field-box, p, table");
        if (content) content.removeAttribute("aria-hidden");
      }
      node = node.parentNode;
    }

    firstError.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /**
   * If the "Last Change Summary" fieldset contains real diff content
   * (not just the placeholder text), remove the "collapsed" class so it
   * is visible without requiring a manual click.
   */
  function autoExpandLastChangeSummary() {
    // Django admin renders fieldset headings with the fieldset title as text.
    var fieldsets = document.querySelectorAll("fieldset");
    fieldsets.forEach(function (fs) {
      var h2 = fs.querySelector("h2");
      if (!h2) return;
      if (h2.textContent.trim() !== "Last Change Summary") return;

      // Check whether the content is the placeholder or real data.
      var placeholder = fs.querySelector(".field-last_change_summary_display span");
      if (placeholder && placeholder.textContent.includes("No change history")) return;

      // Has real content — expand.
      fs.classList.remove("collapsed");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
