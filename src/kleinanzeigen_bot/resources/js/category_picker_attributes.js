// SPDX-FileCopyrightText: © Jens Bergmann and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

// @ts-check

(
  /**
   * Read the picker attributes of a category with the page's own category tree actions.
   *
   * The actions answer with a devalue document: a flat array whose first entry is the root and
   * whose numbers are indices into that array.
   *
   * @param {"getPickerAttributes" | "getDependentPickerAttributes"} action
   * @param {Record<string, string | number>} body
   */
  async (action, body) => {
    const response = await globalThis.fetch(`/_actions/postListingWeb.categoryTree.${action}/`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: globalThis.JSON.stringify(body),
    });
    if (!response.ok) return { statusCode: response.status, attributes: [] };

    /** @type {unknown[]} */
    const flat = globalThis.JSON.parse(await response.text());
    /** @type {Map<number, unknown>} */
    const memo = new globalThis.Map();
    /** @param {number} index @returns {any} */
    const hydrate = (index) => {
      if (index < 0) return undefined;
      if (memo.has(index)) return memo.get(index);
      const value = flat[index];
      if (value === null || typeof value !== "object") return value;
      if (globalThis.Array.isArray(value)) {
        // Tagged values (dates, sets, ...) are not part of the category data.
        if (typeof value[0] === "string") return null;
        /** @type {unknown[]} */
        const list = [];
        memo.set(index, list);
        for (const item of value) list.push(hydrate(item));
        return list;
      }
      /** @type {Record<string, unknown>} */
      const record = {};
      memo.set(index, record);
      for (const [key, item] of globalThis.Object.entries(value)) record[key] = hydrate(/** @type {number} */ (item));
      return record;
    };

    const decoded = hydrate(0);
    const attributes = globalThis.Array.isArray(decoded) ? decoded : [];
    return {
      statusCode: response.status,
      attributes: attributes.map((/** @type {any} */ attribute) => ({
        attributeId: attribute.attributeId,
        required: attribute.required === true,
        options: (attribute.localizedOptions ?? []).map((/** @type {any} */ option) => ({
          value: option.value,
          label: option.localizedValue,
        })),
      })),
    };
  }
);
