// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Fetch a URL with the page's session and return status, headers and text.
   *
   * @param {string} url
   * @param {string} method
   * @param {Record<string, string>} headers
   */
  (url, method, headers) =>
    fetch(url, {
      method: method,
      redirect: "follow",
      headers: headers,
    })
      .then((response) =>
        response.text().then((responseText) => {
          /** @type {Record<string, string>} */
          const responseHeaders = {};
          response.headers.forEach((v, k) => responseHeaders[k] = v);
          return {
            statusCode: response.status,
            statusMessage: response.statusText,
            headers: responseHeaders,
            content: responseText,
          };
        })
      )
);
