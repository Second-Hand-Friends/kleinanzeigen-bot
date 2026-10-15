// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Find the editor's submit or "Nächster Schritt" button and optionally click it.
   *
   * @param {"inspect" | "submit" | "next"} action
   * @param {string[]} labels submit labels in priority order
   * @returns {"package" | "submit" | "next" | boolean}
   */
  (action, labels) => {
    if (location.pathname.startsWith("/p-anzeigentypauswahl/")) return "package";
    /** @param {HTMLElement} element */
    const visible = (element) =>
      element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== "hidden" && !element.matches(":disabled");
    // The editor's action buttons live in the native form around #ad-title.
    const scope = document.getElementById("ad-title")?.closest("form") || document;
    const buttons = [...scope.querySelectorAll("button")].filter(visible);
    // Labels in priority order: a secondary save button earlier in the DOM must not win.
    let submit = null;
    for (const label of labels) {
      submit = buttons.find((button) => button.textContent.trim().includes(label));
      if (submit) break;
    }
    if (submit) {
      if (action === "inspect") return "submit";
      if (action !== "submit") return false;
      submit.click();
      return true;
    }
    // New vehicle/real-estate ads continue to a package selection step instead.
    const next = buttons.find((button) => button.textContent.replace(/\s+/g, " ").trim() === "Nächster Schritt");
    if (next) {
      if (action === "inspect") return "next";
      if (action !== "next") return false;
      next.click();
      return true;
    }
    return false;
  }
);
