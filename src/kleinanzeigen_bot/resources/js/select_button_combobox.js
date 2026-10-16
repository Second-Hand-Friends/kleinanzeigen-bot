// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Open a <button role="combobox"> and click the option matching an API value.
   *
   * @param {string} elemId id of the combobox button
   * @param {string} value API value, matched via React props, option attributes, then normalized text
   * @param {number} pollDeadlineMs how long to wait for the options to appear
   */
  async function (elemId, value, pollDeadlineMs) {
    const btn = document.getElementById(elemId);
    if (!btn) return { ok: false, reason: "button_not_found" };
    btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    /** @type {Element[] | null} */
    let options = null;
    const pollDeadline = Date.now() + pollDeadlineMs;
    while (Date.now() < pollDeadline) {
      await new Promise(function (r) {
        setTimeout(r, 20);
      });
      /** @type {Element | null} */
      let candidate = document.getElementById(elemId + "-menu");
      if (!candidate && btn.parentElement) {
        candidate = btn.parentElement.querySelector('[role="listbox"]');
      }
      if (!candidate && btn.parentElement) {
        const m = btn.parentElement.querySelector('[role="menu"]');
        if (m) candidate = m;
      }
      /* Headless UI / React portals: try aria-controls/owns association first,
           then visible document-level portal candidates with aria-labelledby. */
      if (!candidate && btn.getAttribute) {
        const controlledId = btn.getAttribute("aria-controls");
        if (controlledId) {
          candidate = document.getElementById(controlledId);
        }
      }
      if (!candidate && btn.getAttribute) {
        const ownedId = btn.getAttribute("aria-owns");
        if (ownedId) {
          candidate = document.getElementById(ownedId);
        }
      }
      if (!candidate) {
        const portalCandidates = document.querySelectorAll('[role="listbox"],[role="menu"]');
        for (let p = 0; p < portalCandidates.length; p++) {
          const pc = portalCandidates[p];
          const rect = pc.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            const labelledby = pc.getAttribute("aria-labelledby");
            if (!labelledby || labelledby === btn.id) {
              candidate = pc;
              break;
            }
          }
        }
      }
      if (candidate) {
        options = Array.from(candidate.querySelectorAll('[role="option"]'));
        if (options.length > 0) break;
      }
    }
    if (!options || options.length === 0) {
      return { ok: false, reason: "no_options_after_opening", options: [] };
    }
    const optionInfo = options.map(function (o) {
      return o.textContent ? o.textContent.replace(/\s+/g, " ").trim() : "";
    });
    const fiberKey = Object.keys(btn).find(function (k) {
      return k.startsWith("__reactFiber");
    });
    if (fiberKey) {
      // React internals are untyped.
      let fiber = /** @type {any} */ (btn)[fiberKey];
      for (let j = 0; j < 20 && fiber; j++, fiber = fiber.return) {
        if (fiber.memoizedProps && fiber.memoizedProps.options) {
          const opts = fiber.memoizedProps.options;
          for (let k = 0; k < opts.length; k++) {
            if (opts[k].value === value && options[k]) {
              // @ts-expect-error js-debt: a [role="option"] element need not be an HTMLElement with click()
              options[k].click();
              return { ok: true };
            }
          }
          break;
        }
      }
    }
    for (let k = 0; k < options.length; k++) {
      const o = options[k];
      if (
        o.getAttribute("value") === value ||
        o.getAttribute("data-value") === value ||
        o.getAttribute("aria-label") === value ||
        o.getAttribute("title") === value
      ) {
        // @ts-expect-error js-debt: a [role="option"] element need not be an HTMLElement with click()
        o.click();
        return { ok: true };
      }
      const text = (o.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (text === (value + "").replace(/\s+/g, " ").trim().toLowerCase()) {
        // @ts-expect-error js-debt: a [role="option"] element need not be an HTMLElement with click()
        o.click();
        return { ok: true };
      }
    }
    return { ok: false, reason: "option_not_found", requested: value, options: optionInfo };
  }
);
