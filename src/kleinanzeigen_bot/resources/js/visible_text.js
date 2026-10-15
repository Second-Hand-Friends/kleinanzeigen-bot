// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /** Return the text a user would select in the element. @param {Element} elem */
  function (elem) {
    // A document with a browsing context always has a selection.
    const sel = /** @type {Selection} */ (globalThis.getSelection());
    sel.removeAllRanges();
    const range = document.createRange();
    range.selectNode(elem);
    sel.addRange(range);
    const visibleText = sel.toString().trim();
    sel.removeAllRanges();
    return visibleText;
  }
);
