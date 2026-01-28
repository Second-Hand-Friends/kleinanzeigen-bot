# Documentation Index

This directory contains detailed documentation for kleinanzeigen-bot users and contributors.

## User Documentation

- [Configuration](./CONFIGURATION.md) - Complete reference for `config.yaml`, including all configuration options, timeouts, browser settings, and update check configuration.

- [Ad Configuration](./AD_CONFIGURATION.md) - Complete reference for ad YAML files, including automatic price reduction, description prefix/suffix, and shipping options.

- [Browser Troubleshooting](./BROWSER_TROUBLESHOOTING.md) - Troubleshooting guide for browser connection issues, including Chrome 136+ security requirements, remote debugging setup, and common solutions.

- [Update Check Feature](./UPDATE_CHECK.md) - Information about the update check feature, including configuration, state file format, and error handling.

## Contributor Documentation

Contributor documentation is located in the main repository:

- [CONTRIBUTING.md](../CONTRIBUTING.md) - Development setup, workflow, code quality standards, testing requirements, and contribution guidelines.

- [TESTING.md](./TESTING.md) - Detailed testing strategy, test types (unit/integration/smoke), and execution instructions for contributors.

## Getting Started

New users should start with the [README](../README.md), then refer to these documents for detailed configuration and troubleshooting information.

### Quick Start (3 steps)

1. Install and run the app from the [README](../README.md).
2. Generate `config.yaml` with `kleinanzeigen-bot create-config` and review defaults in [Configuration](./CONFIGURATION.md).
3. Verify your setup with `kleinanzeigen-bot verify`, then publish with `kleinanzeigen-bot publish`.

### Common Troubleshooting Tips

- Browser connection issues: confirm remote debugging settings and Chrome 136+ requirements in [Browser Troubleshooting](./BROWSER_TROUBLESHOOTING.md).
- Update checks not running: verify `update_check.enabled` and `update_check.interval` in [Update Check Feature](./UPDATE_CHECK.md).
