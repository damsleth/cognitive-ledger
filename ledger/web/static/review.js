/** Keyboard-first controls for the high-throughput review queue. */
(function () {
  "use strict";

  function isEditable(el) {
    return el && (
      el.tagName === "INPUT" ||
      el.tagName === "TEXTAREA" ||
      el.tagName === "SELECT" ||
      el.isContentEditable
    );
  }

  function click(selector) {
    const button = document.querySelector(`#review-stage ${selector}`);
    if (button && !button.disabled) button.click();
    return Boolean(button);
  }

  function toggleEditor() {
    const editor = document.querySelector("[data-review-editor]");
    if (!editor) return;
    editor.open = !editor.open;
    if (editor.open) {
      const title = editor.querySelector('input[name="title"]');
      if (title) title.focus();
    }
  }

  function toggleHelp() {
    const panel = document.querySelector("[data-shortcut-panel]");
    const button = document.querySelector("[data-shortcut-help]");
    if (!panel || !button) return;
    panel.hidden = !panel.hidden;
    button.setAttribute("aria-expanded", String(!panel.hidden));
  }

  document.addEventListener("keydown", function (event) {
    if (isEditable(event.target)) return;
    const key = event.key.toLowerCase();
    let handled = false;

    if (key === "?") {
      toggleHelp();
      handled = true;
    } else if (key === "e") {
      toggleEditor();
      handled = true;
    } else if (key === "s") {
      handled = click('button[name="action"][value="skip"]');
    } else if (key === "x" || key === "n") {
      handled = click('button[name="action"][value="reject"]');
    } else if ((key === "a" || key === "y") && !document.querySelector(".review-options")) {
      handled = click('button[name="action"][value="accept"]');
    } else if (/^[1-9]$/.test(key)) {
      const options = document.querySelectorAll('.review-option[name="answer"]');
      const option = options[Number(key) - 1];
      if (option) {
        option.click();
        handled = true;
      }
    }

    if (handled) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-shortcut-help]")) toggleHelp();
  });

  document.body.addEventListener("htmx:beforeRequest", function () {
    const stage = document.querySelector("#review-stage");
    if (stage) stage.classList.add("is-loading");
  });
})();
