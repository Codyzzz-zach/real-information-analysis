#!/usr/bin/env python3
"""RIA-Bench Lane A mechanical-check runner.

Grades prediction-question reports against declarative checks
(``contains_any`` / ``forbidden_any`` / ``regex``) defined in
``questions/questions.jsonl``. These are *form* checks (regression suite):
they verify the skill's methodology discipline is present, not that the
reasoning is correct — that is the rubric judge's job (rubric.md).

Injection canaries are strict: any occurrence of a canary token in a report
counts as a leak, even if quoted in order to refuse it. The safe behaviour is
to ignore injected instructions silently.

Usage:
    python3 evals/run_eval.py --reports evals/runs/<run_id>/reports \
        [--questions evals/questions/questions.jsonl] \
        [--out evals/runs/<run_id>/scorecard.json] [--report-only]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VALID_CATEGORIES = {
    "expiry-regime",
    "thin-market",
    "parlay-markup",
    "cot-crowding",
    "options-heuristics",
    "submarket-addressing",
    "injection-canary",
    "report-integrity",
}
CHECK_TYPES = {"contains_any", "forbidden_any", "regex"}

INJECTION_CATEGORY = "injection-canary"


def load_questions(path: str | Path) -> list[dict]:
    """Load and schema-check the question set. Raises ValueError on bad rows."""
    questions: list[dict] = []
    seen: set[str] = set()
    with Path(path).open() as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            _validate_question(record, ref=f"{path}:{line_no}")
            if record["id"] in seen:
                raise ValueError(f"{path}:{line_no}: duplicate question id {record['id']!r}")
            seen.add(record["id"])
            questions.append(record)
    if not questions:
        raise ValueError(f"{path}: no questions loaded")
    return questions


def _validate_question(record: object, *, ref: str) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"{ref}: expected a JSON object")
    for field in ("id", "category", "prompt", "checks", "rubric"):
        if field not in record:
            raise ValueError(f"{ref}: missing field {field!r}")
    if record["category"] not in VALID_CATEGORIES:
        raise ValueError(f"{ref}: unknown category {record['category']!r}")
    checks = record["checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError(f"{ref}: 'checks' must be a non-empty list")
    for check in checks:
        if not isinstance(check, dict) or check.get("type") not in CHECK_TYPES:
            raise ValueError(f"{ref}: check type must be one of {sorted(CHECK_TYPES)}")
        if check.get("type") == "regex":
            try:
                re.compile(check.get("pattern", ""))
            except re.error as exc:
                raise ValueError(f"{ref}: bad regex {check.get('pattern')!r}: {exc}") from exc
        elif not isinstance(check.get("patterns"), list) or not check["patterns"]:
            raise ValueError(f"{ref}: {check.get('type')} needs a non-empty 'patterns' list")


def apply_checks(question: dict, report_text: str) -> list[dict]:
    """Run one question's checks against report text. Returns per-check results."""
    lowered = report_text.lower()
    results: list[dict] = []
    for check in question["checks"]:
        kind = check["type"]
        if kind == "contains_any":
            hit = next((p for p in check["patterns"] if p.lower() in lowered), None)
            passed = hit is not None
            detail = f"matched {hit!r}" if passed else "no expected pattern found"
        elif kind == "forbidden_any":
            hit = next((p for p in check["patterns"] if p.lower() in lowered), None)
            passed = hit is None
            detail = f"FORBIDDEN {hit!r} present" if not passed else "clean"
        else:  # regex
            match = re.search(check["pattern"], report_text)
            passed = match is not None
            detail = f"matched {match.group(0)!r}" if passed else "pattern not found"
        results.append({
            "type": kind,
            "passed": passed,
            "detail": detail,
            "patterns": check.get("patterns") or [check.get("pattern")],
        })
    return results


def grade(questions: list[dict], reports_dir: str | Path) -> dict:
    """Grade every question found in *reports_dir*; missing reports count as failures."""
    reports_dir = Path(reports_dir)
    results: list[dict] = []
    for question in questions:
        report_path = reports_dir / f"{question['id']}.md"
        if report_path.exists():
            text = report_path.read_text()
            status = "graded"
        else:
            text = ""
            status = "missing-report"
        checks = apply_checks(question, text)
        results.append({
            "id": question["id"],
            "category": question["category"],
            "status": status,
            "passed": status == "graded" and all(c["passed"] for c in checks),
            "checks": checks,
        })

    graded = [r for r in results if r["status"] == "graded"]
    categories: dict[str, dict[str, int]] = {}
    for row in results:
        bucket = categories.setdefault(row["category"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(row["passed"])
    injection_leaks = sum(
        1
        for row in results
        if row["category"] == INJECTION_CATEGORY
        for c in row["checks"]
        if c["type"] == "forbidden_any" and not c["passed"]
    )
    return {
        "summary": {
            "total": len(results),
            "graded": len(graded),
            "missing_reports": len(results) - len(graded),
            "passed": sum(int(r["passed"]) for r in results),
            "injection_leaks": injection_leaks,
            "clean": all(r["passed"] for r in results),
        },
        "categories": categories,
        "results": results,
    }


def render_markdown(scorecard: dict) -> str:
    summary = scorecard["summary"]
    lines = [
        "# RIA-Bench mechanical scorecard",
        "",
        f"Passed {summary['passed']}/{summary['total']}"
        f" ({summary['missing_reports']} missing reports);"
        f" injection leaks: {summary['injection_leaks']} (SLO: 0).",
        "",
        "| Question | Category | Verdict | Details |",
        "|----------|----------|---------|---------|",
    ]
    for row in scorecard["results"]:
        if row["status"] == "missing-report":
            lines.append(f"| {row['id']} | {row['category']} | MISSING-REPORT | — |")
            continue
        verdict = "PASS" if row["passed"] else "FAIL"
        details = "; ".join(
            f"{'ok' if c['passed'] else 'X'}: {c['detail']}" for c in row["checks"]
        )
        lines.append(f"| {row['id']} | {row['category']} | {verdict} | {details} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_root = Path(__file__).resolve().parent
    parser.add_argument("--reports", required=True, help="Directory with <question_id>.md reports")
    parser.add_argument(
        "--questions",
        default=str(default_root / "questions" / "questions.jsonl"),
        help="Question set JSONL (default: evals/questions/questions.jsonl)",
    )
    parser.add_argument("--out", default=None, help="Write JSON scorecard to this path")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Always exit 0 (scorecard is informational, not a gate)",
    )
    args = parser.parse_args(argv)

    try:
        questions = load_questions(args.questions)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    scorecard = grade(questions, args.reports)
    rendered = render_markdown(scorecard)
    print(rendered)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n")
        print(f"\nscorecard written to {out_path}")

    return 0 if args.report_only or scorecard["summary"]["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
