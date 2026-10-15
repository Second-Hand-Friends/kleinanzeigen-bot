// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /** Collect layout hints of the current page for troubleshooting. */
  () => ({
    astro_islands: document.querySelectorAll("astro-island").length,
    logged_in_marker: !!document.querySelector('[data-testid="logged-in-user"]'),
    category_ids: [...document.querySelectorAll('[id^="cat_"]')].map((e) => e.id).slice(0, 30),
    category_picker: !!document.getElementById("ad-category-picker"),
    inner_width: globalThis.innerWidth,
    ready_state: document.readyState,
  })
);
