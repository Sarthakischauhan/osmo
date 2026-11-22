"""Stop-gap copies of additions proposed upstream in Symphony (branch `feat/durable-runs-sandbox`).

- `durable.py` is byte-identical to `core_server/src/core_server/durable.py`.
- `sandbox/seatbelt.py` is byte-identical to `coding_agent/src/coding_agent/run/seatbelt.py`.

Both import only core_server/core_harness (or nothing), so they run against the rev pinned in
pyproject.toml. Once that branch is merged and the pin moves past it:
- import `DurableRunBackend` and friends from `core_server` and delete `durable.py`;
- in `symphony_runs.SymphonyRunner.command`, launch `symphony stdio --sandbox-github --client osmo`
  instead of wrapping the command with `policy().wrap`, and delete `sandbox/`.
"""
