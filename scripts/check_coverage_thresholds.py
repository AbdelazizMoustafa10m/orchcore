"""Check coverage XML against Phase 4 package thresholds."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TOTAL_MINIMUM = 95.0
FILE_MINIMUMS = {
    "orchcore/recovery/rate_limit.py": 95.0,
    "orchcore/pipeline/engine.py": 85.0,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_xml", type=Path, help="coverage.py XML report path")
    args = parser.parse_args()

    tree = ET.parse(args.coverage_xml)
    root = tree.getroot()
    failures: list[str] = []

    total_percent = _percent(root.attrib["line-rate"])
    if total_percent < TOTAL_MINIMUM:
        failures.append(f"total coverage {total_percent:.2f}% < {TOTAL_MINIMUM:.2f}%")

    for target, minimum in FILE_MINIMUMS.items():
        file_percent = _file_coverage(root, target)
        if file_percent is None:
            failures.append(f"coverage report did not contain {target}")
        elif file_percent < minimum:
            failures.append(f"{target} coverage {file_percent:.2f}% < {minimum:.2f}%")

    if failures:
        print("Coverage threshold failure:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


def _file_coverage(root: ET.Element, target: str) -> float | None:
    for class_element in root.findall(".//class"):
        filename = class_element.attrib.get("filename", "")
        if Path(filename).as_posix().endswith(target):
            return _percent(class_element.attrib["line-rate"])
    return None


def _percent(rate: str) -> float:
    return float(rate) * 100.0


if __name__ == "__main__":
    raise SystemExit(main())
