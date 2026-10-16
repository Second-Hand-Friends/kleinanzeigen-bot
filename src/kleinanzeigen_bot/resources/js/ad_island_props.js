// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /** Return the props of the Astro island holding the ad data, or null. */
  () => {
    // Find the astro-island that contains ad data (imageDetails, userDetails, etc.)
    const islands = document.querySelectorAll("astro-island");
    for (const island of islands) {
      const props = island.getAttribute("props") || "";
      if (props.includes("imageDetails") || props.includes("formattedCreationDate") || props.includes("userDetails")) {
        return props;
      }
    }
    return null;
  }
);
