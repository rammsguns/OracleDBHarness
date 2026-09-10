# Runbooks

Each `.sql` file here is one typed maintenance operation. The header records the
capabilities it needs, the Oracle privileges it assumes, and its risk class.

Two rules apply to everything in this directory:

- A runbook names exactly one target object. There is no "recompile everything"
  operation, and identifier parameters are quoted and allowlisted before they reach
  the database.
- A runbook is paired with a verification query in `../diagnostics/`, and the
  execution record stores the verification result. "It returned without error" is not
  accepted as evidence that the change took effect.

`runbook.health_report` is a composite: it runs several reviewed diagnostics and
assembles one report. It has no SQL file because it issues no statement of its own;
it is defined in `services/api/harness_api/runbooks.py`.

Mutating runbooks are refused on profiles whose environment is `production`. See
`MVP_PLAN.md`, "Scope and acceptance criteria".
