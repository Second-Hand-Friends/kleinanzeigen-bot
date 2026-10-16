// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Set a framework-controlled input value via the native setter and dispatch input and change.
   *
   * @param {string} id
   * @param {string} v
   */
  function (id, v) {
    const el = document.getElementById(id);
    if (!el) return false;
    const tag = el.tagName.toLowerCase();
    const proto = tag === "textarea" ? globalThis.HTMLTextAreaElement : globalThis.HTMLInputElement;
    // Both prototypes define a native value accessor.
    const setter = /** @type {(this: HTMLElement, value: string) => void} */ (
      /** @type {PropertyDescriptor} */ (Object.getOwnPropertyDescriptor(proto.prototype, "value")).set
    );
    setter.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
);
