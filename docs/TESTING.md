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

### Running Smoke, Unit, and Integration Tests

- **Unit tests:**
  - Run with: `pdm run utest` (excludes smoke and integration tests)
  - Coverage: `pdm run utest:cov`
- **Integration tests:**
  - Run with: `pdm run itest` (excludes smoke tests)
  - Coverage: `pdm run itest:cov`
- **Smoke tests:**
  - Run with: `pdm run smoke`
  - Coverage: `pdm run smoke:cov`
- **All tests in order:**
  - Run with: `pdm run test` (runs unit, then integration, then smoke)

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

## Why Use Composite Test Groups?

### Failing Fast and Early Feedback

- **Failing fast:** By running unit tests first, then integration, then smoke tests, CI and contributors get immediate feedback if a foundational component is broken.
- **Critical errors surface early:** If a unit test fails, the job stops before running slower or less critical tests, saving time and resources.
- **CI efficiency:** This approach prevents running hundreds of integration/smoke tests if the application is fundamentally broken (e.g., cannot start, cannot load config, etc.).
- **Clear separation:** Each test group (unit, integration, smoke) is reported and covered separately, making it easy to see which layer is failing.

### Tradeoff: Unified Reporting vs. Fast Failure

- **Unified reporting:** Running all tests in a single pytest invocation gives a single summary of all failures, but does not fail fast on critical errors.
- **Composite groups:** Running groups separately means you may only see the first group's failures, but you catch the most important issues as soon as possible.

### When to Use Which

- **CI:** Composite groups are preferred for CI to catch critical failures early and avoid wasting resources.
- **Local development:** You may prefer a unified run (`pdm run test`) to see all failures at once. Both options can be provided in `pyproject.toml` for flexibility.