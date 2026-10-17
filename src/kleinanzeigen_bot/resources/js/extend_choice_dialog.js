// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Handle the extension choice dialog ("Wie möchtest du deine Anzeige verlängern?").
   *
   * The paid option ("Verlängern & Hochschieben für 2,99 €", value "extend-bumpup") must never be
   * selected: the free option is matched by its value ("extend-only") or its own label text, and any
   * option whose label mentions a price or bump-up is rejected. An option's label is the nearest
   * container holding exactly one option, so a shared parent of both options never counts as free.
   *
   * @param {"state" | "select" | "confirm"} action
   * @param {string} freeText label text of the free option ("Nur verlängern")
   * @param {string} confirmText label of the confirm button ("Anzeige verlängern")
   * @returns {string}
   */
  (action, freeText, confirmText) => {
    const paid = /€|hochschieben/i;
    const radioSelector = 'input[type="radio"], [role="radio"]';
    const dialogSelector = 'dialog[open], [role="dialog"], [aria-modal="true"]';
    /** @param {Element} element */
    const visible = (element) => element.getClientRects().length > 0;
    const dialogs = [...document.querySelectorAll(dialogSelector)]
      .filter((dialog) => visible(dialog) && (dialog.textContent || "").includes(freeText));
    const dialog = dialogs[dialogs.length - 1];

    if (action === "state") {
      if (dialog) return "pending";
      const texts = [...document.querySelectorAll(dialogSelector)].filter(visible)
        .map((element) => element.textContent || "").join(" ");
      return /erfolgreich verlängert|Vielen Dank/i.test(texts) ? "success-dialog" : "closed";
    }
    if (!dialog) return "no-dialog";

    /** @type {HTMLElement[]} */
    const options = [.../** @type {NodeListOf<HTMLElement>} */ (dialog.querySelectorAll(radioSelector))];
    /** @param {HTMLElement} option */
    const labelOf = (option) => {
      if (option.id) {
        const label = dialog.querySelector(`label[for="${CSS.escape(option.id)}"]`);
        if (label) return label.textContent || "";
      }
      /** @type {Element | null} */
      let element = option;
      while (element && element !== dialog) {
        if (element.querySelectorAll(radioSelector).length > 1) break;
        if ((element.textContent || "").trim()) return element.textContent || "";
        element = element.parentElement;
      }
      return option.getAttribute("aria-label") || "";
    };
    /** @param {HTMLElement} option */
    const isChecked = (option) =>
      (option instanceof HTMLInputElement && option.checked) || option.getAttribute("aria-checked") === "true";
    /** @param {HTMLElement} option */
    const value = (option) => option instanceof HTMLInputElement ? option.value : option.getAttribute("value");
    const free = options.filter((option) =>
      (value(option) === "extend-only" || labelOf(option).includes(freeText)) && !paid.test(labelOf(option))
    );
    const others = options.filter((option) => !free.includes(option));

    if (action === "select") {
      if (free.length !== 1) return `free-option-not-unique:${free.length}/${options.length}`;
      if (!isChecked(free[0])) free[0].click();
      if (!isChecked(free[0])) return "free-option-not-selected";
      if (others.some(isChecked)) return "paid-option-selected";
      return "selected";
    }

    // confirm: recheck the selection atomically with the single click.
    if (free.length !== 1 || !isChecked(free[0]) || others.some(isChecked)) return "selection-changed";
    const buttons = [...dialog.querySelectorAll("button")].filter((button) => {
      const text = (button.textContent || "").trim();
      return text.includes(confirmText) && !paid.test(text);
    });
    if (buttons.length !== 1) return `confirm-button-not-unique:${buttons.length}`;
    const button = buttons[0];
    if (button.disabled || button.getAttribute("aria-disabled") === "true") return "confirm-button-disabled";
    button.click();
    return "clicked";
  }
);
