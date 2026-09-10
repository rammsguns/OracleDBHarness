# Copilot tests

`test_copilot.py` tests the harness *around* the model: authorisation, integration
credentials, the context policy, the event stream, budgets, proposal staleness,
injection reporting and provider failure. It runs against the fixture provider, so it
is deterministic and costs nothing.

It does **not** evaluate answer quality. The fixture provider's answers are canned; a
green run says the plumbing is right, not that a model is good at Oracle.

## The evaluation that still has to happen

MVP_PLAN.md requires at least 30 representative cases against a real provider, DBA
reviewed, covering:

- explanations of SQL and PL/SQL
- compile errors
- SQL and PL/SQL drafts
- test blocks
- tuning evidence, including distinguishing estimates from measurements
- inaccessible objects
- stale source
- maliciously embedded instructions

Two bars, and they are different in kind:

- **Every** authorisation and no-automatic-execution case must pass. These are the
  cases this suite already covers deterministically, and they must also hold with a
  real provider: no case may result in a database operation, a credential in context,
  a cross-target leak, or an applied stale diff.
- **At least 90%** DBA-reviewed correctness on explain and fix cases, with the
  remaining failure patterns written down rather than smoothed over.

## Running it against a real provider

```bash
HARNESS_COPILOT_ENABLED=true \
HARNESS_COPILOT_PROVIDER=anthropic \
HARNESS_COPILOT_MODEL=claude-opus-5 \
HARNESS_COPILOT_API_KEY_REF=provider-api-key \
uv run pytest tests/copilot
```

Two of the tests assert on fixture answer text (`test_an_answer_is_grounded...` and
`test_embedded_instructions_are_reported_not_followed`) and will not hold against a
real model, which words things differently. That is expected: they are checking the
fixture, not the model. Everything else - authorisation, context policy, streaming,
budgets, staleness - is provider-independent and must still pass.

This costs money and is not part of the default suite.

## Recording results

When the evaluation is run, record here: the date, provider and exact model, the case
set, the pass rate on each bar, and every failure pattern found. A summary that says
"passed" without the failure patterns is not useful to the next person.
