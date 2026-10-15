// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Mark the condition dialog trigger, a button near a label[for*=".condition"], with data-kab-condition-trigger.
   *
   * @returns {string} JSON with found, id and ariaControls
   */
  () => {
    document.querySelectorAll("[data-kab-condition-trigger]").forEach((el) =>
      el.removeAttribute("data-kab-condition-trigger")
    );
    const labels = document.querySelectorAll('label[for*=".condition"]');
    for (const lbl of labels) {
      // Walk up to find a common ancestor, then search for the button within it
      let ancestor = lbl.parentElement;
      for (let depth = 0; depth < 5 && ancestor; depth++) {
        const btn = ancestor.querySelector('button[aria-haspopup="dialog"], button[aria-haspopup="true"]');
        if (btn) {
          btn.setAttribute("data-kab-condition-trigger", "true");
          return JSON.stringify({
            found: true,
            id: btn.id || "",
            ariaControls: btn.getAttribute("aria-controls") || "",
          });
        }
        ancestor = ancestor.parentElement;
      }
    }
    return JSON.stringify({ found: false });
  }
);
