# Agent Playbook

Local operating guide for contributors and AI agents working on `kleinanzeigen-bot`.

Prefer tracked repo files and CI as the source of truth. If this file conflicts with them, the tracked docs/workflows win. Make minimal, focused changes that match existing patterns.

## Read First

Before making non-trivial changes, review:

- `README.md`
- `CONTRIBUTING.md`
- `docs/TESTING.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/build.yml`
- `.github/workflows/validate-pr-title.yml`

## Hard Repo Rules

- Never hit `kleinanzeigen.de` in tests.
- Write code, comments, and contributor-facing docs in English by default.
- For runtime/user-facing output, follow the translation rules in `CONTRIBUTING.md` and update translations when messages change.
- Keep log message strings in plain English; do **not** wrap `LOG.*`/`logger.*` strings with `_()`, because logging messages are translated by `TranslatingLogger`.
- New Python files need the full SPDX header block from `CONTRIBUTING.md`.
- Use full type hints (Python 3.10+ syntax).
- Catch `TimeoutError` in browser automation paths.
- Never hardcode credentials or secrets.
- Prefer small, simple changes over speculative abstractions.

## Repo Patterns

- Browser automation: follow existing `WebScrapingMixin` patterns and use `ensure()` for validation.
- Logging: use `loggers.get_logger(__name__)`.
- Config and file paths: prefer `pathlib.Path` and existing file helpers.
- For Windows-specific cross-platform path logic, prefer `pathlib.PureWindowsPath` when relevant.
- Pydantic models belong in `src/kleinanzeigen_bot/model/`.
- Tests belong in `tests/unit/`, `tests/integration/`, or `tests/smoke/`.
- Use the repo's registered pytest markers.

### Developer Tools

- `pdm run verify-dom-assumptions` runs `scripts/verify_dom_assumptions.py`, a maintainer-owned local-only live DOM diagnostic for kleinanzeigen.de.
- It touches the live site, is not part of the test suite, and should never be run from CI.
- It defaults to the tracked demo ad fixture set in `tests/fixtures/demo_ads/`.
- Treat it as a diagnostic aid, not a supported test surface.

## Testing Guidance

Add or update tests when changing observable behavior, business logic, error handling, or fixing bugs.

Tests may be skipped for:
- log-only wording changes
- diagnostic-only changes with no behavior impact
- trivial wrappers already covered elsewhere

Testing rules:
- Test behavior, not implementation details.
- Prefer extending existing tests over adding duplicates.
- If you touch nearby tests, clean up obvious stale or duplicate coverage when it is cheap and in scope.
- For smoke tests, prefer simple fakes/dummies over mocks and patching.

`docs/TESTING.md` is the authority for test types, execution, and smoke-test conventions.

## Validation Before Work Is Done

Run in this order:

1. `pdm run format`
2. `pdm run lint` — run the repo's configured lint/type-check suite. Use `pdm run lint:fix` first for auto-fixable ruff issues.
3. `pdm run test`

When changing models or config defaults, also regenerate committed artifacts:

- `pdm run generate-schemas` — regenerates `schemas/*.json`
- `pdm run generate-config` — regenerates `docs/config.default.yaml`
- `pdm run generate-artifacts` — runs both

CI and workflows are the source of truth for the exact required checks, coverage gates, generated-artifact verification, and PR title validation.

## PR Expectations

- PR titles must follow the semantic format enforced by `.github/workflows/validate-pr-title.yml`.
- PR descriptions should use `.github/PULL_REQUEST_TEMPLATE.md` and complete its required sections and checklist.

## Completion Checklist

- [ ] Change is minimal and focused
- [ ] Tests were added or updated if behavior changed
- [ ] Translations were updated if needed
- [ ] Generated artifacts (`schemas/*.json`, `docs/config.default.yaml`) were updated if models or config defaults changed
- [ ] `pdm run format` passes
- [ ] `pdm run lint` passes
- [ ] `pdm run test` passes
- [ ] No unrelated issues introduced
