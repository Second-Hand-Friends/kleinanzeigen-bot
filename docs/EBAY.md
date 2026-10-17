# Publishing local ads on eBay

The optional eBay CLI reads the same local ad files and pictures as the
Kleinanzeigen commands. It uses the official Trading API for Buy It Now and
auction listings. Existing Kleinanzeigen offers stay online. There is no order,
sale, stock, or renewal synchronization between the two marketplaces.

Start in **sandbox**, the default environment. No seller credentials are bundled.
All automated tests use offline responses; a seller must test their own category
and business policies in sandbox before using production.

## Developer and seller setup

1. Create an [eBay developer account](https://developer.ebay.com/) and separate
   sandbox and production application keysets. Copy the public client ID into
   `ebay.client_id` for the selected environment.
2. In the developer portal's User Tokens / OAuth settings, configure an OAuth
   redirect URL and note its **RuName**. Set `ebay.redirect_uri` to that name,
   rather than the URL. The accepted redirect page must let you copy the complete
   callback URL, including `code` and `state`, after consent. A browser on another
   computer works; the CLI does not start a GUI or require a local HTTP server.
3. Create a sandbox test seller for sandbox, or use your production seller for
   production. Set `ebay.account` to that seller's actual eBay username. Network
   commands check the authorized seller through `GetUser` before listing actions.
4. Enable seller business policies and obtain the payment, return and shipping
   policy IDs for that account and marketplace. Configure actual policies; the
   bot does not invent shipping costs or return terms.
5. Run `kleinanzeigen-bot ebay-login`. Follow the authorization URL and paste the
   callback URL into the hidden prompt. The client secret is also requested at a
   hidden prompt. For unattended token refresh, supply
   `EBAY_SANDBOX_CLIENT_SECRET` or `EBAY_PRODUCTION_CLIENT_SECRET` in the process
   environment, using your normal secret-management tooling. Never supply a
   secret, authorization code or token as a command-line argument.

The consent request includes the base Trading scope and `sell.inventory`, required
by the Media API for image uploads. Listing creation uses Trading, including for
auctions. The retired `UploadSiteHostedPictures` call is not used.

OAuth access and refresh tokens are stored in a private directory in the OS user
configuration location, outside the portable workspace and repository. Listing
mappings and the publish journal live in the OS user state location, under
`kleinanzeigen-bot/ebay/listings.sqlite3`. Environments and seller accounts have
separate namespaces. Keep a backup of both the local ad files and this state;
removing the journal removes local duplicate protection. Switching environments
requires the corresponding application credentials, authorization and policies.

## Configuration

The main configuration may contain:

```yaml
ebay:
  environment: sandbox
  account: your_sandbox_seller
  client_id: your_public_sandbox_client_id
  redirect_uri: your_application_RuName
  site_id: 77  # Germany
  currency: EUR
  defaults:
    country: DE
    postal_code: '10115'
    shipping_profile_id: '123'
    return_profile_id: '456'
    payment_profile_id: '789'
```

Add eBay-specific fields to an individual ad:

```yaml
ebay:
  category_id: '31388'  # Example only: choose the correct eBay leaf category
  condition_id: 3000   # Check supported conditions for that category
  aspects:
    Brand: [Example]
  best_offer: true
```

Category IDs, conditions and required item specifics differ between eBay and
Kleinanzeigen. Select them from the current eBay category metadata and complete
any missing details reported by verification. `ebay-prepare` checks the common
local requirements; `ebay-verify` asks eBay to validate category-specific rules.
This initial CLI supports seller business policies rather than inline shipping
or return configurations. Category-specific regulatory fields that cannot be
represented by the available settings must be completed through a supported
workflow before publication; verification will reject incomplete requests.

Buy It Now is the default, with source price and quantity one. Best Offer is
optional and independent of the source's negotiable price setting. Use
`ebay.price` to override the price explicitly. Wanted, giveaway, missing-price
and nonpositive-price sources need a positive eBay override before listing.
Prices are copied numerically; there is no currency conversion.

Auctions require both an explicit starting price and duration:

```yaml
ebay:
  listing_format: auction
  auction_start_price: '9.99'
  auction_duration: Days_7  # Days_3, Days_5, Days_7 or Days_10
  quantity: 1
  best_offer: false
```

The source price is never used as an implicit auction starting price.

## Commands

```sh
kleinanzeigen-bot ebay-prepare --ads=all
kleinanzeigen-bot ebay-publish --ads=all --dry-run
kleinanzeigen-bot ebay-verify --ads=42,43
kleinanzeigen-bot ebay-publish --ads=new
kleinanzeigen-bot ebay-status --ads=all
kleinanzeigen-bot ebay-status --ads=all --refresh
```

- `ebay-prepare` prints the cleaned description, removed-link count, resolved
  local pictures, price, format, quantity and missing requirements.
- `--dry-run` on an eBay command performs only local preparation. It does not
  authenticate, refresh tokens, upload pictures, verify, publish, query online
  status, start a browser or check for application updates.
- `ebay-verify` uploads pictures to eBay Picture Services, then calls
  `VerifyAddFixedPriceItem` or `VerifyAddItem`. It displays estimated listing fees
  and never creates an offer. Uploaded pictures are a network side effect.
- `ebay-publish` prepares and verifies the current local data before publishing.
  The default fee limit is **zero**. To accept a higher estimate, explicitly use
  `--fee-limit=0.35`, for example, in the configured currency. The limit applies
  per listing and conservatively includes all returned listing fee components.
  No paid listing enhancements are selected. Estimates are not a guarantee of
  final eBay charges; transaction fees and later changes are outside this check.
- `ebay-status` shows the source identity, eBay item ID and cached status.
  `--refresh` queries eBay and also attempts to reconcile pending submissions.

Selectors default to `new`: sources without a completed eBay mapping, including
unresolved publish intents. `all` includes mapped sources; comma-separated IDs
refer to the source **Kleinanzeigen** IDs. Existing mappings are skipped during
publication, including ended listings. There is no implicit relist action.
Inactive local ads are skipped during preparation/publication, but can appear in
status output. `--ads=all` status also includes journal entries for missing files.
Commands may be scheduled externally; there is no background daemon.

## Description and image handling

All links are removed from the **entire final description**, including the
configured prefix and suffix. This deliberately includes eBay-internal links.
HTML and Markdown targets, visible URLs, `www` addresses, recognizable bare
domains and encoded links are removed; meaningful visible labels and paragraph
breaks are retained. Active HTML is removed and the remaining text is escaped
before sending it to eBay. Preparation shows the exact cleaned text and link
count. The original Kleinanzeigen text and image files remain unchanged.
Pictures are uploaded separately through the Media API `createImageFromFile` method; their API
URL fields are retained and are never embedded as links in the description.

## Identity, concurrency and recovery

The first preparation adds an `ebay_source_id` to each selected source file.
Keep this value when renaming or moving the ad. It is independent of the
Kleinanzeigen `id`; eBay settings and identity do not affect the Kleinanzeigen
content hash. Redownload retains eBay metadata even when
`download.preserve_local_settings` is disabled. An external ID alias registry
also recovers identity if the same source is downloaded into a different folder.
Create a fresh source file without copied identity when intentionally listing a
different physical item.

Before submission, a durable journal reserves the source and persists a unique
Trading API `Item.UUID`. Concurrent publication attempts cannot both submit the
same source. Successful publication stores the returned item ID separately from
the Kleinanzeigen ID. If the response is uncertain or the local write fails,
the reservation remains. Subsequent runs search **all pages** of the seller's
listings around the submission time using the stored source marker; this works
for auctions and fixed-price items. An empty, delayed or failed lookup never
causes another Add request. Run `ebay-status --ads=all --refresh` later. Unresolved
Add requests remain blocked for inspection; do not delete journal
rows or identities to bypass this guard. A complete API rejection is recorded
separately: after correction and an empty reconciliation, a rerun can retry with
the same durable UUID. For old submissions outside eBay's
available history, manual investigation may be necessary.

## API references

- [AddFixedPriceItem](https://developer.ebay.com/devzone/xml/docs/reference/ebay/AddFixedPriceItem.html)
- [VerifyAddFixedPriceItem](https://developer.ebay.com/devzone/xml/docs/reference/ebay/VerifyAddFixedPriceItem.html)
- [AddItem](https://developer.ebay.com/devzone/xml/docs/reference/ebay/AddItem.html)
- [VerifyAddItem](https://developer.ebay.com/devzone/xml/docs/reference/ebay/VerifyAddItem.html)
- [Media API createImageFromFile](https://developer.ebay.com/api-docs/commerce/media/resources/image/methods/createImageFromFile)
- [GetSellerList](https://developer.ebay.com/devzone/xml/docs/reference/ebay/GetSellerList.html)
- [OAuth tokens](https://developer.ebay.com/api-docs/static/oauth-tokens.html)
