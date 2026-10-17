// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Find the editor's "Entwurf speichern" button, optionally click it, or report the saved state.
   *
   * The button sits right next to "Anzeige aufgeben", so it is matched by its exact text inside the
   * native form around #ad-title, never by position. Exactly one visible, enabled match is required.
   *
   * @param {"inspect" | "click" | "confirmed"} action
   * @param {string} label exact button text ("Entwurf speichern")
   * @param {string} confirmation text of the confirmation dialog ("Entwurf gespeichert")
   * @returns {"draft" | "clicked" | "no-form" | `matches:${number}` | boolean}
   */
  (action, label, confirmation) => {
    if (action === "confirmed") {
      return [...document.querySelectorAll('[role="dialog"], dialog[open], h1, h2, h3')]
        .some((element) => (element.textContent || "").includes(confirmation));
    }
    /** @param {HTMLElement} element */
    const visible = (element) =>
      element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== "hidden" && !element.matches(":disabled");
    const form = document.getElementById("ad-title")?.closest("form");
    if (!form) return "no-form";
    const matches = [...form.querySelectorAll("button")].filter(visible)
      .filter((button) => button.textContent.replace(/\s+/g, " ").trim() === label);
    if (matches.length !== 1) return `matches:${matches.length}`;
    if (action === "inspect") return "draft";
    matches[0].click();
    return "clicked";
  }
);
