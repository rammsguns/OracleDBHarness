"""Command line for the copilot evaluation. See tests/copilot/README.md."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tests.copilot.eval.budget import Pricing
from tests.copilot.eval.cases import DEFAULT_CASE_FILE, CaseSetError, load_case_set
from tests.copilot.eval.runner import (
    QUALIFICATION,
    REHEARSAL,
    EvaluationRefused,
    RunConfig,
    run_evaluation,
)
from tests.copilot.eval.scoring import ReportError, render_markdown, score


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.copilot.eval",
        description="Evaluate the copilot against a real model provider (NP-04).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Check the case set. Calls nothing.")
    validate.add_argument("--cases", type=Path, default=DEFAULT_CASE_FILE)

    rehearse = commands.add_parser(
        "rehearse",
        help="Run every case with the fixture provider. Free; never qualification evidence.",
    )
    rehearse.add_argument("--cases", type=Path, default=DEFAULT_CASE_FILE)
    rehearse.add_argument("--report", type=Path, required=True)

    run = commands.add_parser(
        "run", help="Qualification run against a real provider. Spends money."
    )
    run.add_argument("--cases", type=Path, default=DEFAULT_CASE_FILE)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--provider", default="anthropic")
    run.add_argument("--model", required=True, help="Exact model identifier, e.g. claude-opus-5.")
    run.add_argument(
        "--api-key-env",
        default="ANTHROPIC_API_KEY",
        help="Environment variable holding the provider key. The key is never written.",
    )
    run.add_argument("--budget-usd", type=float, required=True, help="Spend ceiling for the run.")
    run.add_argument("--input-usd-per-mtok", type=float, required=True)
    run.add_argument("--output-usd-per-mtok", type=float, required=True)
    run.add_argument(
        "--pricing-source",
        default="",
        help="Where the prices came from and when, for the report.",
    )
    run.add_argument(
        "--approved-context",
        required=True,
        help="Comma-separated context categories the data-sharing approval covers.",
    )
    run.add_argument("--approval-reference", required=True)
    run.add_argument("--operator", default="", help="Who ran this, for the report.")
    run.add_argument("--max-output-tokens", type=int, default=8000)
    run.add_argument("--request-timeout-seconds", type=float, default=300.0)
    run.add_argument(
        "--allow-partial-run",
        action="store_true",
        help="Start even if the ceiling cannot cover every case. Such a run cannot qualify.",
    )

    score_parser = commands.add_parser(
        "score", help="Apply the gates to a reviewed report and write a Markdown summary."
    )
    score_parser.add_argument("report", type=Path)
    score_parser.add_argument(
        "--markdown", type=Path, help="Defaults to the report path with a .md suffix."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            case_set = load_case_set(args.cases)
            print(json.dumps(case_set.summary(), indent=2))
            return 0
        if args.command == "score":
            return _score(args)
        case_set = load_case_set(args.cases)
    except CaseSetError as exc:
        print(f"Case set rejected: {exc}", file=sys.stderr)
        return 2

    if args.command == "rehearse":
        config = RunConfig(mode=REHEARSAL, provider="fake", report_path=args.report)
    else:
        config = RunConfig(
            mode=QUALIFICATION,
            report_path=args.report,
            provider=args.provider,
            model=args.model,
            case_file=args.cases,
            api_key_env=args.api_key_env,
            budget_usd=args.budget_usd,
            pricing=Pricing(
                input_usd_per_mtok=args.input_usd_per_mtok,
                output_usd_per_mtok=args.output_usd_per_mtok,
                source=args.pricing_source,
            ),
            approved_categories=frozenset(
                item.strip() for item in args.approved_context.split(",") if item.strip()
            ),
            approval_reference=args.approval_reference,
            operator=args.operator,
            max_output_tokens=args.max_output_tokens,
            request_timeout_seconds=args.request_timeout_seconds,
            allow_partial_run=args.allow_partial_run,
        )

    try:
        report = asyncio.run(run_evaluation(config, case_set))
    except EvaluationRefused as exc:
        print("Refused before sending anything:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    verdicts: dict[str, int] = {}
    for case in report["cases"]:
        verdicts[case["execution"]] = verdicts.get(case["execution"], 0) + 1
    print(f"Report: {args.report}")
    print(f"Mode: {report['mode']}. {report['evidence']}")
    print(f"Cases: {verdicts}")
    print(f"Estimated spend: ${report['budget']['estimatedSpendUsd']:.4f}")
    if report["aborted"]:
        print(f"ABORTED: {report['aborted']}", file=sys.stderr)
        return 1
    if config.mode == QUALIFICATION:
        print("Next: DBA review of the report, then `score`. See reviewInstructions in the report.")
    return 0


def _score(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    try:
        result = score(report)
    except ReportError as exc:
        print(f"Report rejected: {exc}", file=sys.stderr)
        return 2
    markdown_path = args.markdown or args.report.with_suffix(".md")
    markdown_path.write_text(render_markdown(report, result), encoding="utf-8")
    print(f"{'QUALIFIED' if result['qualified'] else 'NOT QUALIFIED'}; summary: {markdown_path}")
    for reason in result["reasons"]:
        print(f"  - {reason}")
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    sys.exit(main())
