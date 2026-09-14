"""Real-harness qualification preparation for the DataForge adapter (NP-05).

``integrations/dataforge/test/adapter.test.ts`` checks the adapter against a stubbed
``fetch``; it has never made a real HTTP call. This package starts the actual harness
API as a separate process, over the fake Oracle backend and the fixture copilot
provider, issues it a real ``dataforge`` integration credential, and runs the real
adapter and route-registration code in ``integrations/dataforge/src`` against that
live process - including through a proxy, to exercise the streaming path DataForge's
own backend sits behind.

What this does **not** establish, because none of it is present here:

* A real OracleDataForge checkout. The Node side under test is this repository's own
  adapter code, not DataForge's application.
* A real model provider. The copilot is enabled with the fixture provider only.
* A real Oracle database, or a real network/compression proxy such as the one in
  front of a deployed pilot.

See ``integrations/dataforge/COMPATIBILITY.md`` for what remains owed for NP-05, and
``NEXT_PHASE_PLAN.md`` for the gate itself.

Run with:

    uv run python -m tests.dataforge_live run
"""
