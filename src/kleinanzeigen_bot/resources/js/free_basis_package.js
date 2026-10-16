// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Inspect, select or submit the free Basis package on the package selection page.
   *
   * @param {"inspect" | "select" | "submit"} action
   */
  (action) => {
    /** @param {Element | null | undefined} element */
    const visible = (element) =>
      element && element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== "hidden";
    // Non-HTML elements (e.g. SVG) have no innerText and yield "".
    /** @param {Element | null | undefined} element */
    const text = (element) =>
      (/** @type {HTMLElement | null | undefined} */ (element)?.innerText || "").replace(/\s+/g, " ").trim();
    if (!globalThis.location.pathname.startsWith("/p-anzeigentypauswahl/")) return { state: "waiting" };
    const radios = [.../** @type {NodeListOf<HTMLButtonElement>} */ (document.querySelectorAll('button[role="radio"]'))]
      .filter(visible);
    const candidates = radios.filter((element) => text(element) === "Basis Paket");
    if (!candidates.length) return { state: "waiting" };
    if (candidates.length !== 1) return { state: "unsafe" };
    const basis = candidates[0];
    // The Basis card is the largest container holding this radio but no other
    // package, so another package's price can neither prove nor hide its own.
    /** @type {HTMLElement} */
    let card = basis;
    while (
      card.parentElement && card.parentElement !== document.body &&
      !radios.some((element) => element !== basis && /** @type {HTMLElement} */ (card.parentElement).contains(element))
    ) card = card.parentElement;
    const cardText = text(card);
    const freePrice = [...card.querySelectorAll("*")].some((element) =>
      visible(element) &&
      text(element) === "Kostenlos" && ![...element.children].some((child) => text(child) === "Kostenlos")
    );
    // Free means an explicit "Kostenlos" price and no amount anywhere in the card;
    // marketing copy is deliberately not part of the check.
    const free = freePrice && !/\d[\d.,]*\s*(?:€|EUR)/i.test(cardText);
    if (!free || basis.disabled) return { state: "unsafe" };
    if (action === "select" && basis.getAttribute("aria-checked") !== "true") {
      basis.click();
      return { state: "waiting" };
    }
    const selected = basis.getAttribute("aria-checked") === "true";
    if (selected && radios.some((element) => element !== basis && element.getAttribute("aria-checked") === "true")) {
      return { state: "unsafe" };
    }
    const buttons = [...document.querySelectorAll("button")].filter((element) =>
      visible(element) &&
      !element.disabled && text(element) === "Anzeige aufgeben"
    );
    if (action === "submit") {
      // Recheck the chosen free option atomically with the final click.
      if (!selected || buttons.length !== 1) return { state: "unsafe" };
      buttons[0].click();
      return { state: "submitted" };
    }
    return { state: "ready", selected, submitReady: buttons.length === 1 };
  }
);
