// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Click the [role=option] whose normalized text matches the value.
   *
   * @param {HTMLElement} element the listbox
   * @param {string} value
   */
  function (element, value) {
    /** @param {string | null | undefined} s */
    const normalize = (s) => (s ?? "").replace(/\s+/g, " ").trim().toLowerCase();
    const needle = normalize(value);
    const items = element.querySelectorAll('[role="option"]');
    for (const li of items) {
      // A [role="option"] element need not be an HTMLElement (e.g. SVG) and then has no click().
      if (!(li instanceof globalThis.HTMLElement)) continue;
      if (normalize(li.textContent) === needle) {
        try {
          li.scrollIntoView({ block: "nearest" });
        } catch {
          // scrolling is best effort
        }
        li.click();
        return true;
      }
    }
    return false;
  }
);
