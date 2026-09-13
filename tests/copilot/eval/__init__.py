"""Real-provider evaluation of the copilot: the case set, the runner and its scoring.

Deliberately not a pytest suite. A run calls a paid model provider, so it only happens
when someone asks for it: ``uv run python -m tests.copilot.eval --help``. The checks
that keep the runner honest -- fixture refusal, budgets, provider failures, report
completeness, redaction -- are ordinary deterministic tests in
``tests/copilot/test_evaluation_runner.py``. See ``tests/copilot/README.md``.
"""
