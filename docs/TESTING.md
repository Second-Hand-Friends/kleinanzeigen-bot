# TESTING.md

## Test Strategy and Types

This project uses a layered testing approach, with a focus on reliability and fast feedback. The test types are:

- **Unit tests**: Isolated, fast tests targeting the smallest testable units (functions, classes) in isolation. Run first.
- **Integration tests**: Tests that verify the interaction between components or with real external dependencies. Run after unit tests.
- **Smoke tests**: Minimal set of critical checks, run after a successful build and (optionally) after deployment. Their goal is to verify that the most essential workflows (e.g., app starts, config loads, login page reachable) work and that the system is stable enough for deeper testing. Smoke tests are not end-to-end (E2E) tests and should not cover full user workflows.

### Principles

- **Test observable behavior, not internal implementation**
- **Avoid mocks** in smoke tests; use custom fake components (e.g., dummy browser/page objects)
- **Write tests that verify outcomes**, not method call sequences
- **Keep tests simple and maintainable**

### Fakes vs. Mocks

- **Fakes**: Lightweight, custom classes that simulate real dependencies (e.g., DummyBrowser, DummyPage)
- **Mocks**: Avoided in smoke tests; no patching, MagicMock, or side_effect trees

### Example Smoke Tests

- Minimal checks that the application starts and does not crash
- Verifying that a config file can be loaded without error
- Checking that a login page is reachable (but not performing a full login workflow)

### Why This Approach?

- Lower maintenance burden
- Contributors can understand and extend tests
- Quick CI feedback on whether the bot still runs at all

## Smoke Test Marking and Execution

### Marking Smoke Tests

- All smoke tests **must** be marked with `@pytest.mark.smoke`.
- Place smoke tests in `tests/smoke/` for discoverability.
- Example:

```python
import pytest

@pytest.mark.smoke
@pytest.mark.asyncio
async def test_bot_starts(smoke_bot):
    ...
```

### Running Tests

- **Unified run (default, quiet):**
  - `pdm run test` runs all tests in a single invocation with reduced output and coverage enabled.
  - `pdm run test tests/unit/test_file.py::test_name` targets specific tests.
  - `pdm run test -k "pattern"` filters tests by expression.
- **Unified run (verbose):**
  - `pdm run test -v` enables verbose pytest output.
  - `pdm run test -vv` enables one additional verbosity level.
  - `pdm run test:verbose` is equivalent to `pdm run test -v`.
- **Split runs (targeted/stable):**
  - `pdm run utest` runs unit tests only (excludes smoke and integration tests).
  - `pdm run itest` runs integration tests only (excludes smoke tests, serial via `-n 0` for browser stability).
  - `pdm run smoke` runs smoke tests only.

### Coverage

- `pdm run test` includes coverage with `--cov-report=term-missing`.
- `pdm run test:cov:unified` remains the quality-gate unified coverage command.
- `pdm run utest:cov`, `pdm run itest:cov`, and `pdm run smoke:cov` keep per-group coverage outputs for CI uploads.

### Parallel Execution and Slow-Test Tracking

- `pytest-xdist` runs every invocation with `-n auto`, so the suite is split across CPU cores automatically.
- Pytest now reports the slowest 25 tests (`--durations=25 --durations-min=0.5`), making regressions easy to spot in CI logs.
- Long-running scenarios are tagged with `@pytest.mark.slow` (smoke CLI checks and browser integrations). Keep them in CI, but skip locally via `pytest -m "not slow"` when you only need a quick signal.
- Coverage commands (`pdm run test:cov`, etc.) remain compatible—`pytest-cov` merges the per-worker data transparently.

### CI Test Order

- CI runs unit tests first, then integration tests, then smoke tests.
- Coverage for each group is uploaded separately to Codecov (with flags: `unit-tests`, `integration-tests`, `smoke-tests`).
- This ensures that foundational failures are caught early and that test types are clearly separated.

### Adding New Smoke Tests

- Add new tests to `tests/smoke/` and mark them with `@pytest.mark.smoke`.
- Use fakes/dummies for browser and page dependencies (see `tests/conftest.py`).
- Focus on minimal, critical health checks, not full user workflows.

### Why This Structure?

- **Fast feedback:** Unit and integration tests catch most issues before running smoke tests.
- **Separation:** Unit, integration, and smoke tests are not polluted by each other.
- **Coverage clarity:** You can see which code paths are covered by each test type in Codecov.

See also: `pyproject.toml` for test script definitions and `.github/workflows/build.yml` for CI setup.

## Why Offer Both Unified and Split Runs?

### Unified Runs (Default)

- **Single summary:** See all failing tests in one run while developing locally.
- **Coverage included:** The default `pdm run test` command reports coverage without needing a second command.
- **Lower command overhead:** One pytest startup for the whole suite.

### Split Runs (CI and Targeted Debugging)

- **Fail-fast flow in CI:** Unit, integration, and smoke runs are executed in sequence for faster failure feedback.
- **Stable browser integrations:** `pdm run itest` keeps serial execution with `-n 0`.
- **Separate coverage uploads:** CI still uses per-group coverage files/flags for Codecov.

### Tradeoff

- Unified default uses `-n auto`; this can increase integration-test flakiness compared to serial integration runs.
- When stability matters for integration debugging, run `pdm run itest` directly.
