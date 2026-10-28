import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PACKAGE = "airgap_scribe"
THRESHOLD_KEYS = {"lines", "branches"}


class ThresholdError(Exception):
    pass


def percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def summarize(summaries: Sequence[dict[str, Any]]) -> tuple[float, float]:
    lines = percent(
        sum(s["covered_lines"] for s in summaries),
        sum(s["num_statements"] for s in summaries),
    )
    branches = percent(
        sum(s.get("covered_branches", 0) for s in summaries),
        sum(s.get("num_branches", 0) for s in summaries),
    )
    return lines, branches


def module_summaries(files: dict[str, Any], module: str) -> list[dict[str, Any]]:
    single_file = f"{PACKAGE}/{module}.py"
    package_dir = f"{PACKAGE}/{module}/"
    selected = []
    for name, data in files.items():
        normalized = "/" + name.replace("\\", "/")
        if normalized.endswith("/" + single_file) or ("/" + package_dir) in normalized:
            selected.append(data["summary"])
    return selected


def load_threshold(path: Path) -> tuple[float, float]:
    with path.open("rb") as handle:
        values = tomllib.load(handle)
    unknown = set(values) - THRESHOLD_KEYS
    missing = THRESHOLD_KEYS - set(values)
    if unknown or missing:
        raise ThresholdError(
            f"{path}: expected exactly the keys {sorted(THRESHOLD_KEYS)}, "
            f"missing {sorted(missing)}, unknown {sorted(unknown)}"
        )
    for key in sorted(THRESHOLD_KEYS):
        value = values[key]
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ThresholdError(f"{path}: '{key}' must be a number")
    return float(values["lines"]), float(values["branches"])


def check_gate(
    label: str,
    lines: float,
    branches: float,
    minimum_lines: float,
    minimum_branches: float,
) -> list[str]:
    print(
        f"{label}: lines {lines:.2f}% (min {minimum_lines:g}%), "
        f"branches {branches:.2f}% (min {minimum_branches:g}%)"
    )
    failures = []
    if lines < minimum_lines:
        failures.append(f"{label}: lines {lines:.2f}% < {minimum_lines:g}%")
    if branches < minimum_branches:
        failures.append(f"{label}: branches {branches:.2f}% < {minimum_branches:g}%")
    return failures


def check(
    report: dict[str, Any],
    thresholds_dir: Path,
    total_lines: float | None,
    total_branches: float | None,
) -> list[str]:
    failures: list[str] = []
    if total_lines is not None or total_branches is not None:
        totals = report["totals"]
        lines, branches = summarize([totals])
        failures += check_gate("total", lines, branches, total_lines or 0, total_branches or 0)
    for threshold_file in sorted(thresholds_dir.glob("*.toml")):
        module = threshold_file.stem
        minimum_lines, minimum_branches = load_threshold(threshold_file)
        summaries = module_summaries(report["files"], module)
        if not summaries:
            failures.append(f"{module}: no coverage data found")
            continue
        lines, branches = summarize(summaries)
        failures += check_gate(module, lines, branches, minimum_lines, minimum_branches)
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce coverage thresholds per module.")
    parser.add_argument("--coverage", type=Path, default=Path("coverage.json"))
    parser.add_argument("--thresholds", type=Path, default=Path("ci/coverage-thresholds"))
    parser.add_argument("--total-lines", type=float)
    parser.add_argument("--total-branches", type=float)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.coverage.read_text(encoding="utf-8"))
        failures = check(report, args.thresholds, args.total_lines, args.total_branches)
    except (OSError, ValueError, ThresholdError) as error:
        print(f"coverage check error: {error}", file=sys.stderr)
        return 2
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
