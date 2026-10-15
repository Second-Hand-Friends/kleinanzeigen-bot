// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /** Tell whether "Nächster Schritt" led to the package page or the no-image hint. */
  () =>
    globalThis.location.pathname.startsWith("/p-anzeigentypauswahl/")
      ? "package"
      : [...document.querySelectorAll("button")].some((button) =>
          button.textContent.trim() === "Ohne Bild veröffentlichen"
        )
      ? "hint"
      : null
);
