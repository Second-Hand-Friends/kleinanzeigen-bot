// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
// @ts-check

(
  /**
   * Call the messagebox API gateway with the page's session, as the web app's inbox does.
   *
   * @param {string} url absolute gateway URL
   * @param {string} method HTTP method
   * @param {string} token access token from /m-access-token.json, sent as the Authorization header
   * @param {string | null} body JSON request body, or null for none
   */
  (url, method, token, body) => {
    /** @type {Record<string, string>} */
    const headers = { Accept: "application/json", Authorization: token, "X-ECG-USER-AGENT": "messagebox-1" };
    if (body !== null) headers["Content-Type"] = "application/json";
    return globalThis.fetch(url, {
      method: method,
      mode: "cors",
      credentials: "include",
      headers: headers,
      body: body === null ? undefined : body,
    }).then((response) =>
      response.text().then((responseText) => ({
        statusCode: response.status,
        statusMessage: response.statusText,
        content: responseText,
      }))
    );
  }
);
