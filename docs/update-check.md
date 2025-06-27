# Update Check Feature

## Overview

The update check feature automatically checks for newer versions of the bot on GitHub. It supports two channels:
- `latest`: Only final releases
- `prerelease`: Includes pre-releases

## Configuration

```yaml
update_check:
  enabled: true  # Enable/disable update checks
  channel: latest  # One of: latest, prerelease
  interval: 7d    # Check interval (e.g. 7d for 7 days)
```

### Interval Format

The interval is specified as a number followed by a unit:
- `s`: seconds
- `m`: minutes
- `h`: hours
- `d`: days
- `w`: weeks

Examples:
- `7d`: Check every 7 days
- `12h`: Check every 12 hours
- `1w`: Check every week

Validation rules:
- Minimum interval: 1 day (`1d`)
- Maximum interval: 4 weeks (`4w`)
- Value must be positive
- Only supported units are allowed

## State File

The update check state is stored in `.temp/update_check_state.json`. The file format is:

```json
{
  "version": 1,
  "last_check": "2024-03-20T12:00:00+00:00"
}
```

### Fields

- `version`: Current state file format version (integer)
- `last_check`: ISO 8601 timestamp of the last check (UTC)

### Migration

The state file supports version migration:
- Version 0 to 1: Added version field
- Future versions will be migrated automatically

### Timezone Handling

All timestamps are stored in UTC:
- When loading:
  - Timestamps without timezone are assumed to be UTC
  - Timestamps with timezone are converted to UTC
- When saving:
  - All timestamps are converted to UTC before saving
  - Timezone information is preserved in ISO 8601 format

### Edge Cases

The following edge cases are handled:
- Missing state file: Creates new state file
- Corrupted state file: Creates new state file
- Invalid timestamp format: Logs warning, uses current time
- Permission errors: Logs warning, continues without saving
- Invalid interval format: Logs warning, performs check
- Interval too short/long: Logs warning, performs check

## Error Handling

The update check feature handles various error scenarios:
- Network errors: Logs error, continues without check
- GitHub API errors: Logs error, continues without check
- Version parsing errors: Logs error, continues without check
- State file errors: Logs error, creates new state file
- Permission errors: Logs error, continues without saving

## Logging

The feature logs various events:
- Check results (new version available, up to date, etc.)
- State file operations (load, save, migration)
- Error conditions (network, API, parsing, etc.)
- Interval validation warnings
- Timezone conversion information