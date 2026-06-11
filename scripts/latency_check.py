#!/usr/bin/env python3
"""Measure cold vs warm per-label /api/verify latency."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    raise SystemExit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

DEFAULT_IMAGE = ROOT / "sample-data" / "label-pass-old-tom.png"
DEFAULT_APP = {
    "beverageType": "spirits",
    "brandName": "OLD TOM DISTILLERY",
    "classType": "Kentucky Straight Bourbon Whiskey",
    "alcoholContent": "45% Alc./Vol. (90 Proof)",
    "netContents": "750 mL",
    "producer": "Old Tom Distillery, Louisville, KY",
    "countryOfOrigin": "United States",
}


def _find_image(explicit: Path | None) -> Path:
    if explicit and explicit.is_file():
        return explicit
    for candidate in (
        DEFAULT_IMAGE,
        ROOT / "test images" / "01-domestic-bourbon-pass.png",
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit("No test image found. Pass --image.")


def wait_health(base_url: str, timeout_s: float = 120) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=5)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise SystemExit(f"Health check failed: {base_url}/health")


def verify_once(base_url: str, image_path: Path, app: dict) -> dict:
    wall_start = time.perf_counter()
    with image_path.open("rb") as fh:
        r = requests.post(
            f"{base_url}/api/verify",
            files={"image": (image_path.name, fh, "image/png")},
            data={"application": json.dumps(app)},
            timeout=120,
        )
    wall_ms = int((time.perf_counter() - wall_start) * 1000)
    r.raise_for_status()
    body = r.json()
    return {
        "wallMs": wall_ms,
        "processingTimeMs": int(body.get("processingTimeMs") or 0),
        "overall": body.get("overall"),
    }


def maybe_restart(compose_file: Path) -> None:
    print("Restarting container…")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "restart", "ttb"],
        check=True,
        cwd=compose_file.parent,
    )
    time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cold/warm latency check for /api/verify")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--image", type=Path, default=None)
    parser.add_argument("--budget-ms", type=int, default=5000, help="Max processingTimeMs for warm verify")
    parser.add_argument("--restart", action="store_true", help="docker compose restart before first verify")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=ROOT / "docker-compose.yml",
    )
    args = parser.parse_args()

    image = _find_image(args.image)
    if args.restart:
        maybe_restart(args.compose_file)

    wait_health(args.base_url)

    first = verify_once(args.base_url, image, DEFAULT_APP)
    second = verify_once(args.base_url, image, DEFAULT_APP)

    print()
    print(f"{'Request':<12} {'processingTimeMs':>18} {'wallMs':>10} {'overall':>8}")
    print("-" * 52)
    print(f"{'first':<12} {first['processingTimeMs']:>18} {first['wallMs']:>10} {first['overall']:>8}")
    print(f"{'second':<12} {second['processingTimeMs']:>18} {second['wallMs']:>10} {second['overall']:>8}")
    print()

    warm_ms = second["processingTimeMs"]
    if warm_ms > args.budget_ms:
        print(f"FAIL: warm processingTimeMs {warm_ms} exceeds budget {args.budget_ms}")
        return 1
    print(f"PASS: warm processingTimeMs {warm_ms} <= {args.budget_ms}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
