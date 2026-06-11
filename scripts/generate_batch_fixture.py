#!/usr/bin/env python3
"""Generate N copied label images + CSV manifest for batch load testing."""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

DEFAULT_SOURCE = ROOT / "sample-data" / "label-pass-old-tom.png"
DEFAULT_OUT = ROOT / "batch_test"

BASE_ROW = {
    "beverageType": "spirits",
    "brandName": "OLD TOM DISTILLERY",
    "classType": "Kentucky Straight Bourbon Whiskey",
    "alcoholContent": "45% Alc./Vol. (90 Proof)",
    "netContents": "750 mL",
    "producer": "Old Tom Distillery, Louisville, KY",
    "countryOfOrigin": "United States",
    "governmentWarning": (
        "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
        "alcoholic beverages during pregnancy because of the risk of birth defects. "
        "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
        "operate machinery, and may cause health problems."
    ),
}

COLUMNS = [
    "itemId",
    "imageFile",
    "beverageType",
    "brandName",
    "classType",
    "alcoholContent",
    "netContents",
    "producer",
    "countryOfOrigin",
    "governmentWarning",
]


def _find_source(explicit: Path | None) -> Path:
    if explicit and explicit.is_file():
        return explicit
    for candidate in (
        DEFAULT_SOURCE,
        ROOT / "test images" / "01-domestic-bourbon-pass.png",
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit("No source PNG found. Pass --source or add sample-data/label-pass-old-tom.png")


def _row_for_index(i: int) -> dict[str, str]:
    row = dict(BASE_ROW)
    row["itemId"] = f"batch-{i:03d}"
    row["imageFile"] = f"label_{i:03d}.png"
    if i % 10 == 0:
        row["alcoholContent"] = "99% Alc./Vol. (198 Proof)"
    if i % 15 == 0:
        row["brandName"] = "Old Tom Distillery"
    return row


def expected_counts(count: int) -> dict[str, int]:
    """Approximate verdict mix from CSV tweaks (fail wins over review on overlap)."""
    fail = review = 0
    for i in range(1, count + 1):
        if i % 10 == 0:
            fail += 1
        elif i % 15 == 0:
            review += 1
    return {"pass": count - fail - review, "review": review, "fail": fail, "total": count}


def generate(count: int, out_dir: Path, source: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(1, count + 1):
        dest = out_dir / f"label_{i:03d}.png"
        shutil.copy2(source, dest)
        rows.append(_row_for_index(i))

    manifest_path = out_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    counts = expected_counts(count)
    print(f"Wrote {count} images and {manifest_path}")
    print(f"Expected verdicts (from CSV tweaks): {counts}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate batch test fixture (N images + manifest)")
    parser.add_argument("--count", type=int, default=300, help="Number of label files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory")
    parser.add_argument("--source", type=Path, default=None, help="Source PNG to copy")
    args = parser.parse_args()

    source = _find_source(args.source)
    print(f"Source image: {source}")
    generate(args.count, args.out, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
