# TESTING.md

## Smoke Test Strategy

This project uses a layered testing approach, with a strong focus on high-level smoke tests to ensure core workflows function as expected. The goal is to:

- Validate that the bot can run end-to-end without crashing
- Check that critical paths (e.g., config loading, login, ad publishing) work with minimal setup
- Provide fast, understandable feedback for contributors and CI

### Principles

- **Test observable behavior, not internal implementation**
- **Avoid mocks** where possible; use custom fake components (e.g., dummy browser/page objects)
- **Write tests that verify outcomes**, not method call sequences
- **Keep tests simple and maintainable**

### Fakes vs. Mocks

- **Fakes**: Lightweight, custom classes that simulate real dependencies (e.g., DummyBrowser, DummyPage)
- **Mocks**: Avoided in smoke tests; no patching, MagicMock, or side_effect trees

### Example Smoke Tests

- `test_bot_runs_without_crashing`: Verifies that the core workflow doesn't raise
- `test_ad_config_can_be_processed`: Checks that a simple ad config doesn't break the flow

See `tests/smoke/` for examples.

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
  async def test_bot_runs_without_crashing(smoke_bot):
      ...
  ```

### Running Smoke, Unit, and Integration Tests

- **Smoke tests:**
  - Run with: `pdm run smoke`
  - Coverage: `pdm run smoke:cov`
- **Unit tests:**
  - Run with: `pdm run utest` (excludes smoke and integration tests)
  - Coverage: `pdm run utest:cov`
- **Integration tests:**
  - Run with: `pdm run itest` (excludes smoke tests)
  - Coverage: `pdm run itest:cov`
- **All tests in order:**
  - Run with: `pdm run test` (runs smoke, then unit, then integration)

### CI Test Order

- CI runs smoke tests first, then unit tests, then integration tests.
- Coverage for each group is uploaded separately to Codecov (with flags: `smoke-tests`, `unit-tests`, `integration-tests`).
- This ensures that critical path failures are caught early and that test types are clearly separated.

### Adding New Smoke Tests

- Add new tests to `tests/smoke/` and mark them with `@pytest.mark.smoke`.
- Use fakes/dummies for browser and page dependencies (see `tests/conftest.py`).
- Focus on end-to-end flows and observable outcomes.

### Why This Structure?

- **Fast feedback:** Smoke tests catch catastrophic failures before running slower or more detailed tests.
- **Separation:** Unit and integration tests are not polluted by smoke tests, and vice versa.
- **Coverage clarity:** You can see which code paths are covered by smoke, unit, or integration tests in Codecov.

See also: `pyproject.toml` for test script definitions and `.github/workflows/build.yml` for CI setup.

## Why Use Composite Test Groups?

### Failing Fast and Early Feedback

- **Failing fast:** By running smoke tests first, then unit, then integration tests (as separate groups), CI and contributors get immediate feedback if a critical path is broken.
- **Critical errors surface early:** If a smoke test fails, the job stops before running slower or less critical tests, saving time and resources.
- **CI efficiency:** This approach prevents running hundreds of unit/integration tests if the application is fundamentally broken (e.g., cannot start, cannot load config, etc.).
- **Clear separation:** Each test group (smoke, unit, integration) is reported and covered separately, making it easy to see which layer is failing.

### Tradeoff: Unified Reporting vs. Fast Failure

- **Unified reporting:** Running all tests in a single pytest invocation gives a single summary of all failures, but does not fail fast on critical errors.
- **Composite groups:** Running groups separately means you may only see the first group's failures, but you catch the most important issues as soon as possible.

### When to Use Which

- **CI:** Composite groups are preferred for CI to catch critical failures early and avoid wasting resources.
- **Local development:** You may prefer a unified run (`pdm run test`) to see all failures at once. Both options can be provided in `pyproject.toml` for flexibility.