# Update Check Functionality

The bot includes an automatic update checking mechanism that can be configured to check for new versions at regular intervals.

## Configuration

The update check functionality can be configured in your config file:

```yaml
update_check:
  enabled: true
  channel: latest  # or "preview"
  interval: 7d    # Default interval of 7 days
```

## Interval Format

The `interval` setting controls how often the bot checks for updates. It uses a simple format: `<number><unit>`.

### Supported Units

- `s`: seconds
- `m`: minutes
- `h`: hours
- `d`: days
- `w`: weeks

### Validation Rules

1. **Minimum Value**: The minimum allowed interval is 1 day (`1d`). Any shorter interval will be rejected.
   - Examples of invalid intervals: `12h`, `30m`, `3600s`
   - Examples of valid intervals: `1d`, `24h`, `1440m`, `86400s`

2. **Maximum Value**: The maximum allowed interval is 4 weeks (`4w`). Any longer interval will be rejected.
   - Examples of invalid intervals: `5w`, `35d`, `840h`
   - Examples of valid intervals: `4w`, `28d`, `672h`

3. **Value Requirements**:
   - Must be a positive number
   - Must include a valid unit
   - Must be in the format `<number><unit>`

### Examples

Valid intervals:
```yaml
interval: 1d     # Check daily
interval: 7d     # Check weekly (default)
interval: 2w     # Check every two weeks
interval: 4w     # Check monthly (maximum)
```

Invalid intervals:
```yaml
interval: 12h    # Too short (minimum is 1d)
interval: 5w     # Too long (maximum is 4w)
interval: -1d    # Negative values not allowed
interval: 0d     # Zero not allowed
interval: 1      # Missing unit
interval: d      # Missing value
interval: 1x     # Invalid unit
```

## State File

The update check state is stored in `.temp/update_check_state.json`. This file tracks:
- The version of the state file format
- The last time an update check was performed
- The result of the last check

### State File Format

The state file is stored in JSON format with the following structure:
```json
{
    "version": 1,
    "last_check": "2024-03-20T12:00:00+00:00"
}
```

### Version Tracking

The state file includes a version number to support future format changes:
- Version 0: Initial format (no version field)
- Version 1: Added version field and improved error handling

The bot automatically migrates state files from older versions to the current version when loading.

### Error Handling

The state file is automatically created and managed by the bot. If there are any issues with the state file:
1. Invalid JSON: The bot will create a new state file
2. Invalid date format: The bot will reset the last check time
3. Permission errors: The bot will log a warning and continue
4. Other errors: The bot will log a warning and continue

You don't need to modify the state file manually.

## Timezone Handling

All timestamps in the state file are stored in UTC to ensure consistent behavior across different timezones. The bot automatically handles timezone conversions when checking intervals.

### Timezone Features

1. **UTC Storage**: All timestamps are stored in UTC format in the state file
2. **Automatic Conversion**: Timestamps are automatically converted to UTC when:
   - Loading from state file
   - Saving to state file
   - Comparing intervals
3. **Timezone-Aware**: The bot handles timestamps with or without timezone information:
   - If a timestamp has no timezone, it's assumed to be UTC
   - If a timestamp has a different timezone, it's converted to UTC
4. **DST Handling**: The bot correctly handles Daylight Saving Time changes by:
   - Using total seconds for all interval comparisons
   - Ensuring consistent behavior across DST transitions
   - Maintaining accurate intervals regardless of DST changes

### Examples

```json
// State file with UTC timestamp
{
    "version": 1,
    "last_check": "2024-03-20T12:00:00+00:00"
}

// State file with local timestamp (automatically converted to UTC)
{
    "version": 1,
    "last_check": "2024-03-20T14:00:00+02:00"  // Converted to 12:00:00+00:00
}

// State file with naive timestamp (assumed to be UTC)
{
    "version": 1,
    "last_check": "2024-03-20T12:00:00"  // Treated as 12:00:00+00:00
}
```

### DST Considerations

The bot handles DST changes transparently:

1. **Spring Forward**: When clocks move forward (e.g., 2:00 AM becomes 3:00 AM):
   - Intervals are calculated using total seconds
   - No duplicate or missing checks occur
   - Time differences are preserved

2. **Fall Back**: When clocks move backward (e.g., 3:00 AM becomes 2:00 AM):
   - Intervals are calculated using total seconds
   - No duplicate or missing checks occur
   - Time differences are preserved

This ensures that update checks occur at the correct intervals regardless of DST changes or timezone differences.

## Error Handling

If the interval configuration is invalid, the bot will:
1. Log a warning message explaining the issue
2. Perform the update check anyway (to ensure updates aren't missed)
3. Continue with normal operation

This ensures that even with an invalid configuration, you won't miss important updates.