// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Handle the extension choice dialog ("Wie möchtest du deine Anzeige verlängern?").
   *
   * The choice dialog is recognized by its radio group, independent of the option labels, so a dialog
   * that only offers the paid option is still treated as a choice dialog and rejected by "select".
   * The paid option ("Verlängern & Hochschieben für 2,99 €", value "extend-bumpup") must never be
   * selected: the free option is matched by its value ("extend-only") or its own label text, and any
   * option with the paid value or a label mentioning a price or bump-up is rejected. An option's label
   * is the nearest container holding exactly one option, so a shared parent of both options never
   * counts as free.
   *
   * "state" returns "choice" while the choice dialog is open, "success" once the page confirms the
   * extension (the "Anzeige wurde erfolgreich verlängert." toast anywhere on the page, or the legacy
   * "Vielen Dank!" dialog), and "none" otherwise. Only "success" is a positive signal.
   *
   * @param {"state" | "select" | "confirm"} action
   * @param {string} freeText label text of the free option ("Nur verlängern")
   * @param {string} confirmText label of the confirm button ("Anzeige verlängern")
   * @param {string} successText text of the success toast ("erfolgreich verlängert")
   * @param {string} legacySuccessText title of the legacy success dialog ("Vielen Dank")
   * @returns {string}
   */
  (action, freeText, confirmText, successText, legacySuccessText) => {
    const doc = globalThis.document;
    const paid = /€|hochschieben/i;
    const paidValue = "extend-bumpup";
    const radioSelector = 'input[type="radio"], [role="radio"]';
    const dialogSelector = 'dialog[open], [role="dialog"], [aria-modal="true"]';
    /** @param {Element} element */
    const visible = (element) => element.getClientRects().length > 0;
    const openDialogs = [...doc.querySelectorAll(dialogSelector)].filter(visible);
    const choiceDialogs = openDialogs.filter((dialog) => dialog.querySelector(radioSelector) !== null);
    const dialog = choiceDialogs[choiceDialogs.length - 1];

    if (action === "state") {
      if (dialog) return "choice";
      const toast = (doc.body?.innerText || "").includes(successText);
      const legacy = openDialogs.some((element) => (element.textContent || "").includes(legacySuccessText));
      return toast || legacy ? "success" : "none";
    }
    if (!dialog) return "no-dialog";

    /** @type {HTMLElement[]} */
    const options = [...dialog.querySelectorAll(radioSelector)]
      .filter((option) => option instanceof globalThis.HTMLElement);
    /** @param {HTMLElement} option */
    const labelOf = (option) => {
      if (option.id) {
        const label = dialog.querySelector(`label[for="${globalThis.CSS.escape(option.id)}"]`);
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
      (option instanceof globalThis.HTMLInputElement && option.checked) ||
      option.getAttribute("aria-checked") === "true";
    /** @param {HTMLElement} option */
    const value = (option) =>
      option instanceof globalThis.HTMLInputElement ? option.value : option.getAttribute("value");
    const free = options.filter((option) =>
      value(option) !== paidValue &&
      (value(option) === "extend-only" || labelOf(option).includes(freeText)) &&
      !paid.test(labelOf(option))
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
