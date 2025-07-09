# Table of Contents

- [Development Setup](#development-setup)
- [Development Notes](#development-notes)
- [Development Workflow](#development-workflow)
- [Testing Requirements](#testing-requirements)
- [Code Quality Standards](#code-quality-standards)
- [Bug Reports](#bug-reports)
- [Feature Requests](#feature-requests)
- [Pull Request Requirements](#pull-request-requirements)
- [Performance Considerations](#performance-considerations)
- [Security and Best Practices](#security-and-best-practices)
- [Licensing](#licensing)
- [Internationalization (i18n) and Translations](#internationalization-i18n-and-translations)

# Contributing

Thanks for your interest in contributing to this project! Whether it's a bug report, new feature, correction, or additional documentation, we greatly value feedback and contributions from our community.

We want to make contributing as easy and transparent as possible. Contributions via [pull requests](#pull-request-requirements) are much appreciated.

Please read through this document before submitting any contributions to ensure your contribution goes to the correct code repository and we have all the necessary information to effectively respond to your request.

## Development Setup

### Prerequisites
- Python 3.10 or higher
- PDM for dependency management
- Git

### Local Setup
1. Fork and clone the repository
2. Install dependencies: `pdm install`
3. Run tests to verify setup: `pdm run test:cov`

## Development Notes

This section provides quick reference commands for common development tasks. See ‘Testing Requirements’ below for more details on running and organizing tests.

- Format source code: `pdm run format`
- Run tests: `pdm run test` (see 'Testing Requirements' below for more details)
- Run syntax checks: `pdm run lint`
- Linting issues found by ruff can be auto-fixed using `pdm run lint:fix`
- Derive JSON schema files from Pydantic data model: `pdm run generate-schemas`
- Create platform-specific executable: `pdm run compile`
- Application bootstrap works like this:
  ```python
  pdm run app
  |-> executes 'python -m kleinanzeigen_bot'
      |-> executes 'kleinanzeigen_bot/__main__.py'
          |-> executes main() function of 'kleinanzeigen_bot/__init__.py'
              |-> executes KleinanzeigenBot().run()
  ```

## Development Workflow

### Before Submitting
1. **Format your code**: Ensure your code is auto-formatted
   ```bash
   pdm run format
   ```
2. **Lint your code**: Check for linting errors and warnings
   ```bash
   pdm run lint
   ```
3. **Run tests**: Ensure all tests pass locally
   ```bash
   pdm run test
   ```
4. **Check code quality**: Verify your code follows project standards
   - Type hints are complete
   - Docstrings are present
   - SPDX headers are included
   - Imports are properly organized
5. **Test your changes**: Add appropriate tests for new functionality
   - Add smoke tests for critical paths
   - Add unit tests for new components
   - Add integration tests for external dependencies

### Commit Messages
Use clear, descriptive commit messages that explain:
- What was changed
- Why it was changed
- Any breaking changes or important notes

Example:
```
feat: add smoke test for bot startup

- Add test_bot_starts_without_crashing to verify core workflow
- Use DummyBrowser to avoid real browser dependencies
- Follows existing smoke test patterns in tests/smoke/
```

## Testing Requirements

This project uses a comprehensive testing strategy with three test types:

### Test Types
- **Unit tests** (`tests/unit/`): Isolated component tests with mocks. Run first.
- **Integration tests** (`tests/integration/`): Tests with real external dependencies. Run after unit tests.
- **Smoke tests** (`tests/smoke/`): Minimal, post-deployment health checks that verify the most essential workflows (e.g., app starts, config loads, login page reachable). Run after integration tests. Smoke tests are not end-to-end (E2E) tests and should not cover full user workflows.

### Running Tests
```bash
# Run all tests in order (unit → integration → smoke)
pdm run test:cov

# Run specific test types
pdm run utest      # Unit tests only
pdm run itest      # Integration tests only
pdm run smoke      # Smoke tests only

# Run with coverage
pdm run utest:cov  # Unit tests with coverage
pdm run itest:cov  # Integration tests with coverage
pdm run smoke:cov  # Smoke tests with coverage
```

### Adding New Tests
1. **Determine test type** based on what you're testing:
   - **Smoke tests**: Minimal, critical health checks (not full user workflows)
   - **Unit tests**: Individual components, isolated functionality
   - **Integration tests**: External dependencies, real network calls

2. **Place in correct directory**:
   - `tests/smoke/` for smoke tests
   - `tests/unit/` for unit tests
   - `tests/integration/` for integration tests

3. **Add proper markers**:
   ```python
   @pytest.mark.smoke      # For smoke tests
   @pytest.mark.itest      # For integration tests
   @pytest.mark.asyncio    # For async tests
   ```

4. **Use existing fixtures** when possible (see `tests/conftest.py`)

For detailed testing guidelines, see [docs/TESTING.md](docs/TESTING.md).

## Code Quality Standards

### File Headers
All Python files must start with SPDX license headers:
```python
# SPDX-FileCopyrightText: © <your name> and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
```

### Import Organization
- Use absolute imports for project modules: `from kleinanzeigen_bot import KleinanzeigenBot`
- Use relative imports for test utilities: `from tests.conftest import SmokeKleinanzeigenBot`
- Group imports: standard library, third-party, local (with blank lines between groups)

### Type Hints
- Always use type hints for function parameters and return values
- Use `Any` from `typing` for complex types
- Use `Final` for constants
- Use `cast()` when type checker needs help

### Documentation

#### Docstrings
- Use docstrings for **complex functions and classes that need explanation**
- Include examples in docstrings for complex functions (see `utils/misc.py` for examples)

#### Comments
- **Use comments to explain your code logic and reasoning**
- Comment on complex algorithms, business logic, and non-obvious decisions
- Explain "why" not just "what" - the reasoning behind implementation choices
- Use comments for edge cases, workarounds, and platform-specific code

#### Module Documentation
- Add module docstrings for packages and complex modules
- Document the purpose and contents of each module

#### Model Documentation
- Use `Field(description="...")` for Pydantic model fields to document their purpose
- Include examples in field descriptions for complex configurations
- Document validation rules and constraints

#### Logging
- Use structured logging with `loggers.get_logger()`
- Include context in log messages to help with debugging
- Use appropriate log levels (DEBUG, INFO, WARNING, ERROR)
- Log important state changes and decision points

#### Examples
```python
def parse_duration(text: str) -> timedelta:
    """
    Parses a human-readable duration string into a datetime.timedelta.

    Supported units:
      - d: days
      - h: hours
      - m: minutes
      - s: seconds

    Examples:
    >>> parse_duration("1h 30m")
    datetime.timedelta(seconds=5400)
    """
    # Use regex to find all duration parts
    pattern = re.compile(r"(\d+)\s*([dhms])")
    parts = pattern.findall(text.lower())

    # Build timedelta from parsed parts
    kwargs: dict[str, int] = {}
    for value, unit in parts:
        if unit == "d":
            kwargs["days"] = kwargs.get("days", 0) + int(value)
        elif unit == "h":
            kwargs["hours"] = kwargs.get("hours", 0) + int(value)
        # ... handle other units
    return timedelta(**kwargs)
```
### Error Handling
- Use specific exception types when possible
- Include meaningful error messages
- Use `pytest.fail()` with descriptive messages in tests
- Use `pyright: ignore[reportAttributeAccessIssue]` for known type checker issues

## Reporting Bugs/Feature Requests

We use GitHub issues to track bugs and feature requests. Please ensure your description is clear and has sufficient instructions to be able to reproduce the issue.

### Bug Reports
When reporting a bug, please ensure you:
- Confirm the issue is reproducible on the latest release
- Clearly describe the expected and actual behavior
- Provide detailed steps to reproduce the issue
- Include relevant log output if available
- Specify your operating system and browser (if applicable)
- Agree to the project's Code of Conduct

This helps maintainers quickly triage and address issues.

### Feature Requests
Include:
- Clear description of the desired feature
- Use case or problem it solves
- Any implementation ideas or considerations

## Pull Request Requirements

Before submitting a pull request, please ensure you:

1. **Work from the latest source on the main branch**
2. **Create a feature branch** for your changes: `git checkout -b feature/your-feature-name`
3. **Format your code**: `pdm run format`
4. **Lint your code**: `pdm run lint`
5. **Run all tests**: `pdm run test`
6. **Check code quality**: Type hints, docstrings, SPDX headers, import organization
7. **Add appropriate tests** for new functionality (smoke/unit/integration as needed)
8. **Write clear, descriptive commit messages**
9. **Provide a concise summary and motivation for the change in the PR**
10. **List all key changes and dependencies**
11. **Select the correct type(s) of change** (bug fix, feature, breaking change)
12. **Complete the checklist in the PR template**
13. **Confirm your contribution can be used under the project license**

See the [Pull Request template](.github/PULL_REQUEST_TEMPLATE.md) for the full checklist and required fields.

To submit a pull request:
- Fork our repository
- Push your feature branch to your fork
- Open a pull request on GitHub, answering any default questions in the interface

GitHub provides additional documentation on [forking a repository](https://help.github.com/articles/fork-a-repo/) and [creating a pull request](https://help.github.com/articles/creating-a-pull-request/)

## Performance Considerations

- **Smoke tests** should be fast (< 1 second each)
- **Unit tests** should be isolated and fast
- **Integration tests** can be slower but should be minimal
- Use fakes/dummies to avoid real network calls in tests

## Security and Best Practices

- Never commit real credentials in tests
- Use temporary files and directories for test data
- Clean up resources in test teardown
- Use environment variables for configuration
- Follow the principle of least privilege in test setup

## Licensing

See the [LICENSE.txt](LICENSE.txt) file for our project's licensing. All source files must include SPDX license headers as described above. We will ask you to confirm the licensing of your contribution.

## Internationalization (i18n) and Translations

- All user-facing output (log messages, print statements, CLI help, etc.) must be written in **English**.
- For every user-facing message, a **German translation** must be added to `src/kleinanzeigen_bot/resources/translations.de.yaml`.
- Use the translation system for all output—**never hardcode German or other languages** in the code.
- If you add or change a user-facing message, update the translation file and ensure that translation completeness tests pass (`tests/unit/test_translations.py`).
- Review the translation guidelines and patterns in the codebase for correct usage.

