# Test Directory Structure

This directory contains all tests for the Kleinanzeigen Bot project. The tests are organized into different categories and levels of testing to ensure comprehensive coverage and maintainability.

## Directory Overview

```
tests/
├── conftest.py           # Global test fixtures and configuration
├── __init__.py          # Makes the tests directory a Python package
├── integration/         # Integration tests
├── unit/               # Unit tests
│   ├── init/          # Initialization and setup tests
│   ├── extract/       # Data extraction tests
│   └── *.py           # General unit tests
```

## Test Categories

### Unit Tests (`tests/unit/`)
Contains all unit tests that verify individual components and functions in isolation.

#### General Unit Tests (`tests/unit/*.py`)
- `test_ads_utils.py` - Tests for utility functions in the ads module
- `test_bot.py` - Core bot functionality tests
- `test_i18n.py` - Internationalization tests
- `test_main.py` - Main entry point tests
- `test_translations.py` - Translation functionality tests
- `test_utils_*.py` - Various utility function tests

#### Initialization Tests (`tests/unit/init/`)
Tests related to system initialization and setup:
- `test_ads_initialization.py` - Ad loading and validation tests
- `test_auth.py` - Authentication tests
- `test_basic.py` - Basic initialization tests
- `test_cli.py` - Command-line interface tests
- `test_config.py` - Configuration loading tests
- `test_localization.py` - Localization setup tests
- `test_logging.py` - Logging setup tests
- `test_publishing.py` - Ad publishing tests

#### Extraction Tests (`tests/unit/extract/`)
Tests for data extraction functionality:
- `test_extract.py` - General extraction tests
- `test_extract_image_*.py` - Image extraction tests
- `test_extract_url*.py` - URL extraction tests
- `test_extract_simple.py` - Simple extraction scenarios

### Integration Tests (`tests/integration/`)
Contains tests that verify multiple components working together:
- `test_web_scraping_mixin.py` - Tests for web scraping functionality

## Test Configuration

- `conftest.py` files contain shared fixtures and test configuration
  - Root `conftest.py`: Global test configuration
  - `unit/init/conftest.py`: Initialization-specific test configuration

## Test Categories Explained

1. **Unit Tests**
   - Test individual components in isolation
   - Fast execution
   - No external dependencies
   - Focus on specific functionality

2. **Integration Tests**
   - Test multiple components working together
   - May require external dependencies
   - Verify system integration
   - More complex scenarios

## Best Practices

When adding new tests:

1. **Test Location**
   - Place unit tests in the appropriate `unit/` subdirectory
   - Place integration tests in `integration/`
   - Use existing subdirectories or create new ones as needed

2. **Test Organization**
   - Keep related tests together
   - Use clear, descriptive test names
   - Follow existing naming conventions
   - Add necessary fixtures to appropriate `conftest.py`

3. **Test Independence**
   - Each test should be independent
   - Clean up after tests
   - Don't rely on test execution order

4. **Documentation**
   - Add docstrings to test classes and functions
   - Explain complex test scenarios
   - Update this README when adding new test categories

## Running Tests

Tests can be run using pytest:

```bash
# Run all tests
pdm test

# Run specific test category
pdm test utest
pdm test itest

# Run specific test file
pdm test tests/unit/test_ads_utils.py
```

## Test Coverage

The test suite aims to maintain high coverage of the codebase. When adding new features or modifying existing ones, ensure appropriate test coverage is maintained or improved.