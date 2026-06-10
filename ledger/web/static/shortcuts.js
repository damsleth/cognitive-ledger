/**
 * shortcuts.js — Keyboard shortcuts for the web UI.
 * Runs after DOM is ready (loaded with defer).
 */
(function () {
  "use strict";

  function isEditable(el) {
    return (
      el.tagName === "INPUT" ||
      el.tagName === "TEXTAREA" ||
      el.isContentEditable
    );
  }

  function focusedRow() {
    return document.querySelector(".note-row:focus, .result-card:focus");
  }

  function navigateList(direction) {
    const items = Array.from(
      document.querySelectorAll(".note-row, .result-card")
    );
    if (!items.length) return;
    const current = focusedRow();
    let idx = current ? items.indexOf(current) : -1;
    idx = Math.max(0, Math.min(items.length - 1, idx + direction));
    items[idx].focus();
    items[idx].scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("keydown", function (e) {
    if (isEditable(e.target)) return;

    switch (e.key) {
      case "/": {
        e.preventDefault();
        const search = document.querySelector(".topbar-search input");
        if (search) search.focus();
        break;
      }
      case "g": {
        window.location.href = "/graph";
        break;
      }
      case "j": {
        navigateList(1);
        break;
      }
      case "k": {
        navigateList(-1);
        break;
      }
      case "Enter": {
        const row = focusedRow();
        if (row) {
          const link = row.querySelector("a");
          if (link) link.click();
        }
        break;
      }
      case "Escape": {
        if (document.activeElement) document.activeElement.blur();
        break;
      }
    }
  });

  // Make list items focusable if they aren't already.
  document.querySelectorAll(".note-row, .result-card").forEach((el) => {
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
  });
})();
