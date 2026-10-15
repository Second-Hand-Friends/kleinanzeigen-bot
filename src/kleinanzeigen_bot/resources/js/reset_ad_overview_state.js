// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Drop the given keys from the overview's stored queryParams and return the removed ones.
   *
   * @param {string[]} keys
   */
  (keys) => {
    try {
      const stored = JSON.parse(sessionStorage.getItem("queryParams") || "null");
      if (!stored || typeof stored !== "object") return [];
      const removed = keys.filter((key) => key in stored);
      removed.forEach((key) => delete stored[key]);
      if (removed.length) sessionStorage.setItem("queryParams", JSON.stringify(stored));
      return removed;
    } catch {
      return [];
    }
  }
);
