// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /** Tell whether the element is a checked checkbox or radio input. @param {HTMLInputElement} element */
  function (element) {
    if (element.tagName.toLowerCase() === "input") {
      if (element.type === "checkbox" || element.type === "radio") {
        return element.checked;
      }
    }
    return false;
  }
);
