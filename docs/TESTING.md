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

- **Canonical unified command:**
  - `pdm run test` runs all tests in one invocation.
  - Output is quiet by default.
  - Coverage is enabled by default with `--cov-report=term-missing`.
- **Verbosity controls:**
  - `pdm run test -v` enables verbose pytest output and durations.
  - `pdm run test -vv` keeps pytest's second verbosity level and durations.
- **Split runs (targeted/stable):**
  - `pdm run utest` runs only unit tests.
  - `pdm run itest` runs only integration tests and stays serial (`-n 0`) for browser stability.
  - `pdm run smoke` runs only smoke tests.
  - Split runs also include coverage by default.

### Coverage

- Local and CI-facing public commands (`test`, `utest`, `itest`, `smoke`) always enable coverage.
- Default local report output remains `term-missing`.
- CI still uploads split XML coverage files (unit/integration/smoke) to Codecov using internal `ci:*` runner commands.

### Parallel Execution and Slow-Test Tracking

- `test`, `utest`, and `smoke` run with `-n auto`.
- `itest` runs with `-n 0` by design to avoid flaky browser parallelism.
- Verbose runs (`-v` and above) report the slowest 25 tests (`--durations=25 --durations-min=0.5`), while quiet/default runs omit durations.
- Long-running scenarios are tagged with `@pytest.mark.slow` (smoke CLI checks and browser integrations). Keep them in CI, but skip locally via `pytest -m "not slow"` when you only need a quick signal.

### CI Test Order

- Split suites run in this order: unit, integration, smoke.
- Internal commands (`ci:coverage:prepare`, `ci:test:unit`, `ci:test:integration`, `ci:test:smoke`) are backed by `scripts/run_tests.py`.
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
For contributor workflow, setup, and submission expectations, see `CONTRIBUTING.md`.

## Why Offer Both Unified and Split Runs?

### Unified Runs (Default)

- **Single summary:** See all failing tests in one run while developing locally.
- **Coverage included:** The default `pdm run test` command reports coverage without needing a second command.
- **Lower command overhead:** One pytest startup for the whole suite.

### Split Runs (CI and Targeted Debugging)

- **Fail-fast flow in CI:** Unit, integration, and smoke runs are executed in sequence for faster failure feedback.
- **Stable browser integrations:** `pdm run itest` keeps serial execution with `-n 0`.
- **Separate coverage uploads:** CI still uses per-group coverage files/flags for Codecov.

### Trade-off

- Unified default uses `-n auto`; this can increase integration-test flakiness compared to serial integration runs.
- When integration-test stability is a concern, run `pdm run itest` directly.
