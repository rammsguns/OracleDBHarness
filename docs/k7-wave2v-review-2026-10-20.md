# Wave 2V self-review — 2026-10-20

**PASS: release one Q5 Trial 28 diagnostic.** This is a self-review by Codex (AI,
not DBA), after the implementation handoff, under the new single-agent execution
model. K-7 remains NOT QUALIFIED; Kiwi stays off by default. No freeze or Q6 release.

## Change and regression coverage

`claim_checks.py` now recognizes object-qualified privileges called roles, inverted
object-privilege classification, and explicit lowercase code-formatted privilege
names. SQL name boundaries preserve genuine role names; ordinary lowercase verbs
remain outside the object-privilege pattern. No case identifiers, model settings,
budgets or rubrics changed. Service behavior uses the existing bounded revision.

`tests/copilot/test_k7_wave2v.py` covers the four reported misses directly and on
both plain and editor/no-change diagnosis paths, plus six positive controls. Each
service regression makes exactly two provider calls (initial answer plus one
revision), retains `No code change:` and the narrow object grant, and emits no
proposal. The later review correction adds one direct and two service controls,
for 21 new tests total. No earlier tests were weakened or removed.

## Deliberate second pass

Fresh `/tmp/probe_wave2v.py`, authored after the initial handoff, initially scored
16/17. `UPDATE ON SCOTT.EMP was granted as a role` escaped detection. The adjacent
classification grammar now includes affirmed `is/was/are granted as a role`.
`granted through a role` and `grant ... to a role` remain valid controls. This
correction was made before the final complete test run and provider release.

The final 17/17 fresh controls include object-qualified UPDATE/DELETE/REFERENCES,
EXECUTE on the package, lowercase code-formatted privilege lists, plural
classification, actual role names, ordinary English, role inclusion, negation and
privileges granted through/to roles. This is lexical regression evidence, not a
proof of all natural-language classifications.

| Verification | Result |
| --- | --- |
| Full copilot/contract/bootstrap pytest command | **1814 passed, 97.02 seconds** |
| Ruff: services tests scripts copilot-eval/apply_trial_reviews.py | Clean |
| git diff --check | Clean |
| /tmp/probe_wave2r.py | 34/34 |
| /tmp/test_wave2r_independent.py | 5/5 |
| /tmp/probe_wave2o.py | 34/34 |
| /tmp/probe_wave2k.py | 40/40 |
| /tmp/probe_wave2l_variations.py | 19/19 |
| /tmp/probe_wave2u.py | **28/28** |
| /tmp/test_wave2u_independent.py | **18/18** |
| /tmp/probe_wave2v.py | **17/17** |

The two retained service suites ran together: 23 passed in 4.41 seconds. Scratch
outputs are `/tmp/wave2v-full-final.out`, `/tmp/wave2v-retained-{r,o,k,l,u,v}.out`,
`/tmp/wave2v-retained-service.out`, and `/tmp/wave2v-adversarial-results.json`.
The preceding full run also passed (1811 tests before adding the three review
regressions). Retained KINJ-01 evidence, line attribution, spec/body isolation and
Kiwi settlement controls all pass.

Implementation and gate used only Python/stand-ins, without Strata or Oracle.
Historical reports, case-set and stand-in data are preserved. Trial 28 must receive
its own D1 compile/cleanup, Oracle checks, secret scan and full rubric/grounding
audit; this gate does not predict those scores.
