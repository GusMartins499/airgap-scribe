import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-module-coverage.py"


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_module_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(lines: int, statements: int, branches: int = 0, total_branches: int = 0) -> dict:
    return {
        "covered_lines": lines,
        "num_statements": statements,
        "covered_branches": branches,
        "num_branches": total_branches,
    }


def report(files: dict[str, dict], totals: dict | None = None) -> dict[str, Any]:
    return {
        "files": {name: {"summary": data} for name, data in files.items()},
        "totals": totals or summary(1, 1),
    }


def run(
    checker: ModuleType,
    tmp_path: Path,
    coverage: dict[str, Any],
    thresholds: dict[str, str],
    *extra: str,
) -> int:
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    thresholds_dir = tmp_path / "thresholds"
    thresholds_dir.mkdir()
    for module, body in thresholds.items():
        (thresholds_dir / f"{module}.toml").write_text(body, encoding="utf-8")
    return checker.main(
        ["--coverage", str(coverage_path), "--thresholds", str(thresholds_dir), *extra]
    )


def test_passes_when_every_module_meets_its_threshold(checker: ModuleType, tmp_path: Path) -> None:
    coverage = report({"src/airgap_scribe/naming.py": summary(19, 20, 9, 10)})

    assert run(checker, tmp_path, coverage, {"naming": "lines = 95\nbranches = 90"}) == 0


def test_fails_when_a_module_misses_its_line_threshold(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = report({"src/airgap_scribe/naming.py": summary(18, 20, 10, 10)})

    assert run(checker, tmp_path, coverage, {"naming": "lines = 95\nbranches = 90"}) == 1
    assert "naming: lines 90.00% < 95%" in capsys.readouterr().err


def test_fails_when_a_module_misses_its_branch_threshold(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = report({"src/airgap_scribe/queue.py": summary(20, 20, 1, 2)})

    assert run(checker, tmp_path, coverage, {"queue": "lines = 95\nbranches = 90"}) == 1
    assert "queue: branches 50.00% < 90%" in capsys.readouterr().err


def test_module_without_branches_counts_as_fully_branch_covered(
    checker: ModuleType, tmp_path: Path
) -> None:
    coverage = report({"src/airgap_scribe/workdir.py": summary(7, 7)})

    assert run(checker, tmp_path, coverage, {"workdir": "lines = 100\nbranches = 100"}) == 0


def test_package_modules_aggregate_every_file_in_the_package(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = report(
        {
            "src/airgap_scribe/anonymize/__init__.py": summary(10, 10, 4, 4),
            "src/airgap_scribe/anonymize/ner.py": summary(0, 10, 0, 4),
            "src/airgap_scribe/anonymizer_extra.py": summary(0, 100, 0, 100),
        }
    )

    assert run(checker, tmp_path, coverage, {"anonymize": "lines = 50\nbranches = 50"}) == 0
    assert "anonymize: lines 50.00%" in capsys.readouterr().out


def test_does_not_confuse_modules_sharing_a_prefix(checker: ModuleType, tmp_path: Path) -> None:
    coverage = report(
        {
            "src/airgap_scribe/queue.py": summary(10, 10),
            "src/airgap_scribe/myqueue.py": summary(0, 10),
        }
    )

    assert run(checker, tmp_path, coverage, {"queue": "lines = 100\nbranches = 100"}) == 0


def test_fails_when_a_module_has_no_coverage_data(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = report({"src/airgap_scribe/naming.py": summary(1, 1)})

    assert run(checker, tmp_path, coverage, {"verify": "lines = 95\nbranches = 90"}) == 1
    assert "verify: no coverage data found" in capsys.readouterr().err


def test_enforces_the_total_gate_when_requested(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = report({}, totals=summary(90, 100, 70, 100))

    code = run(checker, tmp_path, coverage, {}, "--total-lines", "85", "--total-branches", "80")

    assert code == 1
    assert "total: branches 70.00% < 80%" in capsys.readouterr().err


def test_total_gate_passes_when_both_minimums_are_met(checker: ModuleType, tmp_path: Path) -> None:
    coverage = report({}, totals=summary(90, 100, 85, 100))

    code = run(checker, tmp_path, coverage, {}, "--total-lines", "85", "--total-branches", "80")

    assert code == 0


@pytest.mark.parametrize(
    "body",
    ["lines = 95", "lines = 95\nbranches = 90\nfunctions = 1", 'lines = "95"\nbranches = 90'],
)
def test_malformed_threshold_files_are_errors(
    checker: ModuleType, tmp_path: Path, body: str
) -> None:
    coverage = report({"src/airgap_scribe/naming.py": summary(1, 1)})

    assert run(checker, tmp_path, coverage, {"naming": body}) == 2


def test_missing_coverage_report_is_an_error(checker: ModuleType, tmp_path: Path) -> None:
    assert checker.main(["--coverage", str(tmp_path / "absent.json")]) == 2


def test_shipped_threshold_files_are_well_formed(checker: ModuleType) -> None:
    thresholds = sorted((SCRIPT.parents[1] / "ci" / "coverage-thresholds").glob("*.toml"))

    package = SCRIPT.parents[1] / "src" / "airgap_scribe"

    assert {"naming", "queue"} <= {path.stem for path in thresholds}
    for path in thresholds:
        assert (package / f"{path.stem}.py").is_file() or (package / path.stem).is_dir()
        lines, branches = checker.load_threshold(path)
        assert lines >= 80 and branches >= 70
