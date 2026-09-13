"""Capacity measurement for the pilot target: ten users across three databases (NP-07).

``python -m tests.capacity`` drives the API the way the console does - worksheet
sessions, bounded reads, explicit transactions, cancellation and metadata panels - from
many virtual users at once, through a steady phase, a saturation phase and a recovery
phase, and checks that no user's or target's work shows up anywhere it should not.

Nothing is assumed. The workload, durations, resource limits and every acceptance
threshold come from a configuration file (see ``workload.example.json``), and a file
missing any of them is refused. Thresholds are marked ``proposed`` until someone records
who agreed them and when; a report measured against proposed thresholds, against the
stand-in backend, or against fewer than three distinct databases says so and cannot pass
as a pilot qualification.

``rehearse`` runs a short workload against a local API on the stand-in, to check this
tooling and its report. It is not a measurement of anything a pilot would run.

See docs/capacity.md for the runbook.
"""
