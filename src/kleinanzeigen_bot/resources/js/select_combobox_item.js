// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Click the dropdown <li> whose normalized label matches the value.
   *
   * @param {HTMLElement} element the dropdown list
   * @param {string} value
   */
  function (element, value) {
    const selected = String(value);
    /** @param {string | null | undefined} s */
    const normalize = (s) => (s ?? "").replace(/_+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
    // Normalize whitespace and convert to lowercase for comparison

    // Get all <li> elements inside the dropdown
    const items = /** @type {NodeListOf<HTMLLIElement>} */ (
      element.querySelectorAll(':scope > li[role="option"], :scope > li')
    );

    for (const li of items) {
      // The visible label is typically inside the last <span>
      const labelEl = li.querySelector(":scope > span:last-of-type");
      const label = normalize(labelEl ? labelEl.textContent : li.textContent);

      // Compare normalized lowercase values
      if (label === normalize(selected)) {
        // Scroll to make sure the element is visible
        try {
          li.scrollIntoView({ block: "nearest" });
        } catch {
          // scrolling is best effort
        }

        // Click the matched element
        li.click();
        return true;
      }
    }

    // Return false if no matching item was found
    return false;
  }
);
