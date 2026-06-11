#!/usr/bin/env python3
"""Evaluate verifier against test image manifests and api-results benchmark."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
TEST_IMAGES = Path(os.environ.get("TTB_TEST_IMAGES", ROOT / "test images"))
GROUND_TRUTH = SCRIPT_DIR / "ground_truth.json"
API_RESULTS = TEST_IMAGES / "api-results.json"

def _backend_path() -> Path:
    for candidate in (Path("/app/backend"), ROOT / "backend"):
        if candidate.is_dir():
            return candidate
    return ROOT / "backend"


def load_manifests() -> list[dict]:
    cases = []
    for rel in ("manifest.json", "external/javieravitia/manifest.json"):
        path = TEST_IMAGES / rel
        if path.is_file():
            for entry in json.loads(path.read_text()):
                cases.append(entry)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--direct", action="store_true", help="Run in-process without HTTP")
    parser.add_argument("--limit", type=int, default=0, help="Max cases to run (0 = all)")
    args = parser.parse_args()

    gt = json.loads(GROUND_TRUTH.read_text())
    benchmark = {r["filename"]: r for r in json.loads(API_RESULTS.read_text())}
    cases = load_manifests()

    times: list[int] = []
    matched = 0
    failed = []

    if args.direct:
        sys.path.insert(0, str(_backend_path()))
        from verify import verify_image  # noqa: WPS433

    if args.limit:
        cases = cases[: args.limit]

    for case in cases:
        filename = case["filename"]
        img_path = TEST_IMAGES / filename
        if not img_path.is_file():
            print(f"SKIP missing image: {filename}")
            continue
        app = gt.get(filename) or gt.get(Path(filename).name, {})
        data = img_path.read_bytes()
        t0 = time.perf_counter()
        if args.direct:
            result = verify_image(data, app)
        else:
            import requests  # noqa: WPS433

            with img_path.open("rb") as f:
                resp = requests.post(
                    f"{args.base_url.rstrip('/')}/api/verify",
                    files={"image": (Path(filename).name, f, "image/png")},
                    data={"application": json.dumps(app)},
                    timeout=120,
                )
            resp.raise_for_status()
            result = resp.json()
        elapsed = int((time.perf_counter() - t0) * 1000)
        times.append(result.get("processingTimeMs", elapsed))

        expected = benchmark.get(filename, {}).get(
            "expectedOverall", case.get("expectedOverall", "pass")
        )
        actual = result.get("overall", "fail")
        ok = actual == expected
        matched += int(ok)
        mark = "OK" if ok else "FAIL"
        print(f"{mark}  {filename}: expected={expected} actual={actual} ({result.get('processingTimeMs')}ms)")
        if not ok:
            failed.append((filename, expected, actual, result))

    print(f"\nVerdict accuracy: {matched}/{len(cases)}")
    if times:
        times.sort()
        print(f"Latency ms — p50={times[len(times)//2]} p95={times[int(len(times)*0.95)]}")

    if failed:
        print("\nFailures:")
        for fn, exp, act, res in failed:
            print(f"  {fn}: expected {exp}, got {act}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
