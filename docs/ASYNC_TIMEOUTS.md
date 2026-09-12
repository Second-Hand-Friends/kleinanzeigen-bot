# Browser timeout and cancellation contract

Browser helpers accept configured **relative durations in seconds**. An operation
deadline is an **absolute event-loop time**, calculated with
`asyncio.get_running_loop().time()`. Never persist a deadline or treat it as a
configuration value.

## Inventory and preserved budgets

| Path | Budget and retry policy |
| --- | --- |
| `web_find`, `web_find_all`, selector groups | Initial attempt plus `retry_max_attempts` when retries are enabled. Each attempt uses `TimeoutConfig.effective`, including multiplier and exponential backoff. Backoff increases the allowed duration; it does not insert a sleep between attempts. |
| `web_probe` | One base `quick_dom` duration (or override), without multiplier or retries. Local timeout means absence. |
| `web_await` | One effective duration, or the supplied duration when `apply_multiplier=False`. Polls normally every 0.5 seconds; stale-session reattachment uses 0.05 seconds. |
| Selector alternatives | Existing priority allocation within one attempt: primary selector first, then bounded alternatives. |
| `web_open` | One effective `page_load` duration for navigation and readiness, without retries. Best-effort viewport adjustment follows this budget. |
| Auth0 post-submit transition | Separate base `login_detection` budgets for URL detection and selector confirmation, then the existing fallback pause and one base `quick_dom` confirmation budget. These phases are intentionally not collapsed into a single `login_detection` duration. |
| Browser startup | No configured total startup limit. Remote port probes retain their existing retries and socket limits. A caller can explicitly bound the asynchronous startup operation. |
| Browser shutdown | Separate five-second graceful process-exit and two-second kill/reap budgets. These are not a total limit for websocket closure. |
| Synchronous Chrome detection | Existing subprocess/socket limits remain separate from asynchronous deadlines. |

Composite form helpers can perform several lookups. Their relative `timeout`
argument retains its existing meaning for those lookups; it does not implicitly
become a total workflow limit. Human input and diagnostic pauses likewise do not
gain an implicit total timeout.

## Total operation budgets

Use a lexical timeout context when a workflow needs a total budget spanning
multiple helpers or retries:

```python
deadline = asyncio.get_running_loop().time() + total_seconds
async with asyncio.timeout_at(deadline):
    await web.web_find(By.ID, "first")
    await web.web_find(By.ID, "second")
```

Use `asyncio.timeout(duration)` when converting a relative duration at the
boundary. Polling uses an absolute deadline internally so nested asynchronous
work and polling sleeps consume the same budget. Each local timeout boundary
owns its error conversion; redundant equal-deadline wrappers can otherwise
replace selector-specific errors with an empty `TimeoutError`.

A caller's timeout cancels the task through nested helpers, even when those
helpers allow longer individual attempts. Inner retry/probe/fallback handlers
must not interpret that cancellation as a local timeout. The owning outer
context converts its cancellation into `TimeoutError` when it exits.

External task cancellation remains `asyncio.CancelledError`. It must propagate
without retries, login fallbacks, or conversion to selector absence. Cleanup
still runs where the bot owns resources. During startup, nodriver can hold
partially constructed resources before returning a browser; the bot cannot
claim ownership of resources it has not received.

Timeouts are cooperative: they interrupt asynchronous suspension points, not
synchronous blocking code. Code that suppresses cancellation can delay expiry.
A zero budget expires at the first asynchronous suspension; it does not
guarantee an initial asynchronous DOM query. Previously, even a zero budget
could wait for page preparation and a complete initial query before checking
elapsed time.

## Intentional behavior changes and validation

Polling deadlines now cover browser target refresh, predicate evaluation,
and session reattachment. Previously, checking elapsed time only between polls
could leave a single browser call hanging indefinitely. Navigation and readiness
also share the page-load budget instead of beginning that budget after navigation.

Polling awaits target refresh directly instead of nodriver's `await tab` helper.
That helper inserts a half-second pause before every condition check and creates
independent tasks that survive cancellation of its wait. Direct awaiting allows
short selector budgets to perform their first check and cancels refresh work
together with the operation. Subsequent polls retain the explicit polling delay.

Configured durations, retry/backoff policy, timing fields, log messages, and
selector-specific errors remain the compatibility contract. An externally
cancelled attempt is not reported as a completed local timeout attempt.

Behavior tests in `tests/unit/test_web_scraping_mixin.py` and
`tests/unit/test_login_flow.py` use fake browser work to exercise expiry,
cancellation, retry exhaustion, fallback behavior, and resource cleanup. Tests
must never contact `kleinanzeigen.de`.

`ASYNC109` is enabled globally. Its exceptions are limited to browser adapter
modules that deliberately retain configured duration arguments, and the test
modules whose doubles mirror those arguments.

## Troubleshooting

- For recurring selector or page-load `TimeoutError`, inspect the timing data and
  the relevant configured timeout. If a caller supplies a total deadline, increasing
  individual attempt budgets cannot extend it.
- If retries or login fallbacks stop on `CancelledError`, check whether the caller
  cancelled the task or its enclosing deadline expired. This propagation is
  intentional; do not catch cancellation as selector absence.
- For browser connection failures, Chrome 136+ security requirements, remote
  debugging setup, and timeout tuning, see the
  [Browser Troubleshooting Guide](BROWSER_TROUBLESHOOTING.md).
