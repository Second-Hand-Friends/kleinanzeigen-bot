// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Select the <select> option matching a value, else its displayed text, and dispatch change.
   *
   * @param {HTMLSelectElement} element
   * @param {string | number} value
   */
  function (element, value) {
    const wanted = String(value);

    // 1) Try by value
    for (let i = 0; i < element.options.length; i++) {
      if (element.options[i].value === wanted) {
        element.selectedIndex = i;
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
    }

    // 2) Fallback by displayed text (trimmed)
    const needle = wanted.trim();
    for (let i = 0; i < element.options.length; i++) {
      const opt = element.options[i];
      const shown = (opt.label ?? opt.text ?? opt.textContent ?? "").trim();
      if (shown === needle) {
        element.selectedIndex = i;
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
    }

    throw new Error("Option not found by value or displayed text: " + wanted);
  }
);
