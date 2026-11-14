// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

// @ts-check

(
  /** Return a category choice label without screen-reader-only and decorative parts. @param {Element} elem */
  function (elem) {
    const copy = /** @type {Element} */ (elem.cloneNode(true));
    for (const part of copy.querySelectorAll('.sr-only, [aria-hidden="true"]')) {
      part.remove();
    }
    return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
  }
);
