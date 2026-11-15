// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

/** @typedef {{value: string, label: string}} FieldOption */
/** @typedef {{key: string, attribute: string, label: string, required: boolean, preset: string, options: FieldOption[]}} CategoryField */

(
  /**
   * Describe every category-specific field (attributeMap[...]) of the ad form without changing it.
   *
   * @param {number} optionsDelayMs how long to wait for a combobox to render its options
   * @returns {Promise<CategoryField[]>}
   */
  async (optionsDelayMs) => {
    /** @param {number} ms */
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    /** @param {string | null | undefined} value */
    const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
    /**
     * API values of a combobox, read from the React props the same way as select_button_combobox.js.
     *
     * @param {Element} button
     * @returns {{value?: unknown}[] | null}
     */
    const reactOptions = (button) => {
      const fiberKey = Object.keys(button).find((k) => k.startsWith("__reactFiber"));
      // React internals are untyped.
      let fiber = fiberKey ? /** @type {any} */ (button)[fiberKey] : null;
      for (let depth = 0; depth < 20 && fiber; depth++, fiber = fiber.return) {
        if (fiber.memoizedProps && Array.isArray(fiber.memoizedProps.options)) return fiber.memoizedProps.options;
      }
      return null;
    };
    /** @param {Element} button */
    const comboboxOptions = async (button) => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await sleep(optionsDelayMs);
      const listbox = document.getElementById(button.getAttribute("aria-controls") || "") ||
        document.querySelector('[role="listbox"]');
      const labels = listbox ? [...listbox.querySelectorAll('[role="option"]')].map((o) => clean(o.textContent)) : [];
      const values = reactOptions(button);
      // Close the list again without selecting anything.
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return labels
        .map((label, index) => ({
          value: values && values.length === labels.length ? String(values[index]?.value ?? "") : "",
          label,
        }))
        .filter((option) => option.label && option.label !== "Bitte wählen");
    };
    /** @type {CategoryField[]} */
    const fields = [];
    for (const hidden of document.querySelectorAll('input[type="hidden"][name^="attributeMap["]')) {
      const input = /** @type {HTMLInputElement} */ (hidden);
      const key = input.name.slice("attributeMap[".length, -1);
      const name = key.split(".").pop() || key;
      const button = document.getElementById(key);
      const radios = /** @type {HTMLInputElement[]} */ ([
        ...document.querySelectorAll(`input[type="radio"][name="attributeMap[${CSS.escape(key)}]"]`),
      ]);
      const rawLabel = clean(document.querySelector(`label[for="${CSS.escape(key)}"]`)?.textContent) ||
        clean(radios[0]?.closest("fieldset")?.querySelector("legend")?.textContent) || name;
      /** @type {FieldOption[]} */
      let options = [];
      if (radios.length) {
        options = radios.map((radio) => ({
          value: radio.value,
          label: clean(document.querySelector(`label[for="${CSS.escape(radio.id)}"]`)?.textContent),
        }));
      } else if (button?.getAttribute("role") === "combobox") {
        options = await comboboxOptions(button);
      }
      fields.push({
        key,
        attribute: `${name}_s`,
        label: clean(rawLabel.replace(/\(optional\)/i, "")),
        // The form marks optional fields with "(optional)" in the label.
        required: !/\(optional\)/i.test(rawLabel),
        preset: input.value,
        options,
      });
    }
    return fields;
  }
);
