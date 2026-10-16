// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

/** @typedef {HTMLElement & {validationMessage?: string}} Field Only native controls have a validationMessage. */

(
  /** Read visible field errors from the ad form without collecting field values. */
  () => {
    // The live editor (generic and vehicle categories) wraps #ad-title and all
    // controls in one native <form>; without it there is nothing to inspect.
    const form = document.getElementById("ad-title")?.closest("form");
    if (!form) return [];
    /** @param {Element | null | undefined} element */
    const visible = (element) =>
      element && element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== "hidden";
    // Non-HTML elements (e.g. SVG) have no innerText and yield "".
    /** @param {Element | null | undefined} element */
    const text = (element) =>
      (/** @type {HTMLElement | null | undefined} */ (element)?.innerText || "").replace(/\s+/g, " ").trim();
    const labels = [...form.querySelectorAll("label")];
    /** @param {Element} element */
    const labelFor = (element) => {
      /** @type {Element | null} */
      let scope = element;
      for (let depth = 0; depth < 6 && scope && scope !== form; depth++, scope = scope.parentElement) {
        const controls = [scope, ...scope.querySelectorAll("input[id], select[id], textarea[id], button[id]")];
        const matches = new Set();
        for (const control of controls) {
          const label = labels.find((candidate) => control.id && candidate.htmlFor === control.id);
          if (label && text(label)) matches.add(text(label));
        }
        if (matches.size === 1) return [...matches][0];
        if (matches.size > 1) break;
        const localLabels = scope.querySelectorAll("label");
        if (localLabels.length === 1) return text(localLabels[0]);
      }
      return element.matches("input, select, textarea, button") ? element.getAttribute("name") || element.id || "" : "";
    };
    const errors = new Set();
    const reportedElements = new Set();
    /**
     * @param {Element} element
     * @param {string | undefined} message
     */
    const add = (element, message) => {
      if (!message) return;
      const label = labelFor(element);
      errors.add(label ? `${label}: ${message}` : message);
    };
    const fields = /** @type {NodeListOf<Field>} */ (
      form.querySelectorAll('[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid')
    );
    for (const field of fields) {
      if (!visible(field)) continue;
      const errorReferences = field.getAttribute("aria-errormessage");
      const references = errorReferences ||
        (field.getAttribute("aria-invalid") === "true" ? field.getAttribute("aria-describedby") : "") || "";
      const messages = references.trim().split(/\s+/).map((id) => document.getElementById(id))
        .filter((element) => element && form.contains(element) && visible(element))
        // Descriptions can be hints; the live brand control marks its error text-critical.
        .filter((element) =>
          errorReferences || /** @type {HTMLElement} */
          (element).matches('[id$="-error"], [role="alert"], [role="status"], .text-critical')
        )
        .map((element) => {
          reportedElements.add(element);
          return text(element);
        }).filter(Boolean);
      if (!messages.length && !field.validationMessage && field.getAttribute("aria-invalid") === "true") {
        // Some fields expose an adjacent status without an ARIA reference.
        // Only inspect a container holding this one input.
        for (let scope = field.parentElement; scope && scope !== form; scope = scope.parentElement) {
          const controls = [...scope.querySelectorAll("input, select, textarea, button")].filter(visible);
          if (controls.length !== 1) break;
          const statuses = [...scope.querySelectorAll('[role="status"], [role="alert"]')].filter(visible);
          if (statuses.some((status) => text(status))) {
            for (const status of statuses) {
              reportedElements.add(status);
              if (text(status)) messages.push(text(status));
            }
            break;
          }
        }
      }
      add(field, messages.join(" ") || field.validationMessage);
    }
    // The redesigned category controls reuse id="attribute-error" and do not
    // consistently mark their inputs aria-invalid. Read every visible error.
    for (const error of form.querySelectorAll('[id$="-error"]')) {
      if (visible(error) && !reportedElements.has(error)) add(error, text(error));
    }
    return [...errors];
  }
);
