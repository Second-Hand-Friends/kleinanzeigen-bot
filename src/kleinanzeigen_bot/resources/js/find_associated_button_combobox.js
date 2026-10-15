// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Find the id of the <button role="combobox"> belonging to a hidden input.
   *
   * @param {string} hiddenInputName exact name of the hidden input
   */
  function (hiddenInputName) {
    const name = hiddenInputName;

    // Find the specific hidden input by exact name.
    const inp = document.querySelector("input[type='hidden'][name=" + JSON.stringify(name) + "]");
    if (!inp) return null;

    // Derive expected button ID from attributeMap[VALUE].
    const match = name.match(/^attributeMap\[(.+)\]$/);
    if (match) {
      const btn = document.getElementById(match[1]);
      if (btn && btn.getAttribute("role") === "combobox" && btn.tagName === "BUTTON") return match[1];
    }

    // Walk up the DOM tree to find a button[role="combobox"].
    let parent = inp.parentElement;
    for (let i = 0; i < 8 && parent; i++, parent = parent.parentElement) {
      const btn = parent.querySelector('button[role="combobox"]');
      if (btn && btn.id) return btn.id;
    }

    return null;
  }
);
