# Ad Configuration Reference

Complete reference for ad YAML files in kleinanzeigen-bot.

## File Format

Each ad is described in a separate JSON or YAML file with the default `ad_` prefix (for example, `ad_laptop.yaml`). You can customize the prefix via the `ad_files` pattern in `config.yaml`.
Examples below use YAML, but JSON uses the same keys and structure.

Parameter values specified in the `ad_defaults` section of `config.yaml` don't need to be specified again in the ad configuration file.

## Quick Start

Generate sample ad files using the download command:

```bash
# Download all ads from your profile
kleinanzeigen-bot download --ads=all

# Download only new ads (not locally saved yet)
kleinanzeigen-bot download --ads=new

# Download specific ads by ID
kleinanzeigen-bot download --ads=1,2,3
```

For full JSON schema with IDE autocompletion support, see:

- [schemas/ad.schema.json](https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/ad.schema.json)

📖 **[Complete Main Configuration Reference →](CONFIGURATION.md)**

Full documentation for `config.yaml` including all options, timeouts, browser settings, update checks, and ad_defaults.

## Configuration Structure

### Basic Ad Properties

Description values can be multiline. See <https://yaml-multiline.info/> for YAML syntax examples.

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/ad.schema.json
active: true
type: OFFER
title: "Your Ad Title"
description: |
  Your ad description here.
  Supports multiple lines.
```

### Description Prefix and Suffix

You can add prefix and suffix text to your ad descriptions in two ways:

#### New Format (Recommended)

In your `config.yaml` file you can specify a `description_prefix` and `description_suffix` under the `ad_defaults` section:

```yaml
ad_defaults:
  description_prefix: "Prefix text"
  description_suffix: "Suffix text"
```

#### Legacy Format

In your ad configuration file you can specify a `description_prefix` and `description_suffix`:

```yaml
description_prefix: "Prefix text"
description_suffix: "Suffix text"
```

#### Precedence

The ad-level setting has precedence over the `config.yaml` default. If you specify both, the ad-level setting will be used. We recommend using the `config.yaml` defaults as it is more flexible and easier to manage.

### Category

Built-in category name, custom category name from `config.yaml`, or category ID.

```yaml
# Built-in category name (see default list at
# https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml)
category: "Elektronik > Notebooks"

# Custom category name (defined in config.yaml)
category: "Verschenken & Tauschen > Tauschen"

# Category ID
category: 161/278
```

### Price and Price Type

```yaml
price:  # Price in euros; decimals allowed but will be rounded to nearest whole euro on processing
        # (prefer whole euros for predictability)
price_type:  # one of: FIXED, NEGOTIABLE, GIVE_AWAY (default: NEGOTIABLE)
```

### Automatic Price Reduction

When `auto_price_reduction.enabled` is set to `true`, the bot lowers the configured `price` every time the ad is reposted.

**Important:** Price reductions only apply when using the `publish` command (which deletes the old ad and creates a new one). Using the `update` command to modify ad content does NOT trigger price reductions or increment `repost_count`.

`repost_count` is tracked for every ad (and persisted inside the corresponding `ad_*.yaml`) so reductions continue across runs.

`min_price` is required whenever `enabled` is `true` and must be less than or equal to `price`; this makes an explicit floor (including `0`) mandatory. If `min_price` equals the current price, the bot will log a warning and perform no reduction.

**Note:** `repost_count` and price reduction counters are only incremented and persisted after a successful publish. Failed publish attempts do not advance the counters.

```yaml
auto_price_reduction:
  enabled:  # true or false to enable automatic price reduction on reposts (default: false)
  strategy:  # "PERCENTAGE" or "FIXED" (required when enabled is true)
  amount:    # Reduction amount; interpreted as percent for PERCENTAGE or currency units for FIXED
             # (prefer whole euros for predictability)
  min_price:  # Required when enabled is true; minimum price floor
              # (use 0 for no lower bound, prefer whole euros for predictability)
  delay_reposts:  # Number of reposts to wait before first reduction (default: 0)
  delay_days:     # Number of days to wait after publication before reductions (default: 0)
```

**Note:** All prices are rounded to whole euros after each reduction step.

#### PERCENTAGE Strategy Example

```yaml
price: 150
price_type: FIXED
auto_price_reduction:
  enabled: true
  strategy: PERCENTAGE
  amount: 10
  min_price: 90
  delay_reposts: 0
  delay_days: 0
```

This posts the ad at 150 € the first time, then 135 € (−10%), 122 € (−10%), 110 € (−10%), 99 € (−10%), and stops decreasing at 90 €.

**Note:** The bot applies commercial rounding (ROUND_HALF_UP) to full euros after each reduction step. For example, 121.5 rounds to 122, and 109.8 rounds to 110. This step-wise rounding affects the final price progression, especially for percentage-based reductions.

#### FIXED Strategy Example

```yaml
price: 150
price_type: FIXED
auto_price_reduction:
  enabled: true
  strategy: FIXED
  amount: 15
  min_price: 90
  delay_reposts: 0
  delay_days: 0
```

This posts the ad at 150 € the first time, then 135 € (−15 €), 120 € (−15 €), 105 € (−15 €), and stops decreasing at 90 €.

#### Note on `delay_days` Behavior

The `delay_days` parameter counts complete 24-hour periods (whole days) since the ad was published. For example, if `delay_days: 7` and the ad was published 6 days and 23 hours ago, the reduction will not yet apply. This ensures predictable behavior and avoids partial-day ambiguity.

Set `auto_price_reduction.enabled: false` (or omit the entire `auto_price_reduction` section) to keep the existing behavior—prices stay fixed and `repost_count` only acts as tracked metadata for future changes.

You can configure `auto_price_reduction` once under `ad_defaults` in `config.yaml`. The `min_price` can be set there or overridden per ad file as needed.

### Special Attributes

Special attributes are category-specific key/value pairs. Use the download command to inspect existing ads in your category and reuse the keys you see under `special_attributes`.

```yaml
special_attributes:
  # Example for rental properties
  # haus_mieten.zimmer_d: "3"  # Number of rooms
```

### Shipping Configuration

```yaml
shipping_type:  # one of: PICKUP, SHIPPING, NOT_APPLICABLE (default: SHIPPING)
shipping_costs:  # e.g. 2.95 (for individual postage, keep shipping_type SHIPPING and leave shipping_options empty)

# Specify shipping options / packages
# It is possible to select multiple packages, but only from one size (S, M, L)!
# Possible package types for size S:
#   - DHL_2
#   - Hermes_Päckchen
#   - Hermes_S
# Possible package types for size M:
#   - DHL_5
#   - Hermes_M
# Possible package types for size L:
#   - DHL_10
#   - DHL_20
#   - DHL_31,5
#   - Hermes_L
shipping_options: []

# Example (size S only):
# shipping_options:
#   - DHL_2
#   - Hermes_Päckchen

sell_directly:  # true or false, requires shipping_type SHIPPING to take effect (default: false)
```

**Shipping types:**

- `PICKUP` - Buyer picks up the item
- `SHIPPING` - Item is shipped (requires shipping costs or options)
- `NOT_APPLICABLE` - Shipping not applicable for this item

**Sell Directly:**
When `sell_directly: true`, buyers can purchase the item directly through the platform without contacting the seller first. This feature only works when `shipping_type: SHIPPING`.

### Images

List of wildcard patterns to select images. If relative paths are specified, they are relative to this ad configuration file.

```yaml
images:
  # - laptop_*.{jpg,png}
```

### Contact Information

Contact details for the ad. These override defaults from `config.yaml`.

```yaml
contact:
  name:
  street:
  zipcode:
  phone: ""  # IMPORTANT: surround phone number with quotes to prevent removal of leading zeros
```

### Republication Interval

How often the ad should be republished (in days). Overrides `ad_defaults.republication_interval` from `config.yaml`.

```yaml
republication_interval:  # every X days the ad should be re-published (default: 7)
```

### Auto-Managed Fields

The following fields are automatically managed by the bot. Do not manually edit these unless you know what you're doing.

```yaml
id:  # The ID assigned by kleinanzeigen.de
created_on:  # ISO timestamp when the ad was first published
updated_on:  # ISO timestamp when the ad was last published
content_hash:  # Hash of the ad content, used to detect changes
repost_count:  # How often the ad has been (re)published; used for automatic price reductions
```

## Complete Example

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/ad.schema.json
active: true
type: OFFER
title: "Example Ad Title"
description: |
  This is a multi-line description.
  You can add as much detail as you want here.
  The bot will preserve line breaks and formatting.

description_prefix: "For sale: "  # Optional ad-level override; defaults can live in config.yaml
description_suffix: " Please message if interested!"  # Optional ad-level override

category: "Elektronik > Notebooks"

price: 150
price_type: FIXED

auto_price_reduction:
  enabled: true
  strategy: PERCENTAGE
  amount: 10
  min_price: 90
  delay_reposts: 0
  delay_days: 0

shipping_type: SHIPPING
shipping_costs: 4.95
sell_directly: true

images:
  - "images/laptop_*.jpg"

contact:
  name: "John Doe"
  street: "Main Street 123"
  zipcode: "12345"
  phone: "0123456789"

republication_interval: 7
```

## Best Practices

1. **Use meaningful filenames**: Name your ad files descriptively, e.g., `ad_laptop_hp_15.yaml`
1. **Set defaults in config.yaml**: Put common values in `ad_defaults` to avoid repetition
1. **Test before bulk publishing**: Use `--ads=changed` or `--ads=new` to test changes before republishing all ads
1. **Back up your ad files**: Keep them in version control if you want to track changes
1. **Use price reductions carefully**: Set appropriate `min_price` to avoid underpricing
1. **Check shipping options**: Ensure your shipping options match the actual package size and cost

## Troubleshooting

- **Schema validation errors**: Run `kleinanzeigen-bot verify` (binary) or `pdm run app verify` (source) to see which fields fail validation.
- **Price reduction not applying**: Confirm `auto_price_reduction.enabled` is `true`, `min_price` is set, and you are using `publish` (not `update`). Remember ad-level values override `ad_defaults`.
- **Shipping configuration issues**: Use `shipping_type: SHIPPING` when setting `shipping_costs` or `shipping_options`, and pick options from a single size group (S/M/L).
- **Category not found**: Verify the category name or ID and check any custom mappings in `config.yaml`.
- **File naming/prefix mismatch**: Ensure ad files match your `ad_files` glob and prefix (default `ad_`).
- **Image path resolution**: Relative paths are resolved from the ad file location; use absolute paths and check file permissions if images are not found.
