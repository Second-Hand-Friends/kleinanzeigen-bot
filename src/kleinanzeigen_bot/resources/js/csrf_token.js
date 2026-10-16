// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

// The classic manage-ads page exposes the CSRF token as <meta name="_csrf">; the redesigned (Astro)
// page only carries it inside the JSON "data-initialprops" attribute (e.g. #consentBanner) as
// webContext.csrfToken. Both hold the same session token.
(
  /** Return the session's CSRF token from the classic <meta> or the redesigned page props, or null. */
  () => {
    const meta = document.querySelector('meta[name="_csrf"]')?.getAttribute("content");
    if (meta && meta.trim()) return meta;
    for (const el of document.querySelectorAll("[data-initialprops]")) {
      try {
        // The selector guarantees the attribute.
        const token = JSON.parse(/** @type {string} */ (el.getAttribute("data-initialprops")))?.webContext?.csrfToken;
        if (typeof token === "string" && token.trim()) return token;
      } catch {
        // ignore elements with malformed props
      }
    }
    return null;
  }
);
