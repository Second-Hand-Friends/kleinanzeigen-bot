// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /** Detect the redesigned publish success page, which shows no ad ID. */
  () => {
    const bodyText = (document.body?.innerText || "").replace(/\s+/g, " ").trim();
    const hasManageAdsControl = [.../** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll("a, button"))]
      .some((element) => (element.innerText || "").replace(/\s+/g, " ").trim().includes("Zu meinen Anzeigen"));
    return bodyText.includes("Geschafft!") && hasManageAdsControl;
  }
);
