"""Stop-gap copies of core_server additions proposed upstream in Symphony.

`durable.py` and `sandbox/` are byte-identical to `core_server/src/core_server/durable.py` and
`core_server/src/core_server/sandbox/` on Symphony's `feat/durable-runs-sandbox` branch. They only
import core_server and core_harness, so they run unchanged against the rev pinned in pyproject.toml.
Once that branch is merged and the pin moves past it, import them from core_server and delete this
package.
"""
