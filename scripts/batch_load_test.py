#!/usr/bin/env python3
"""Load-test POST /api/batch with N images (single request or chunked)."""

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

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "generate_batch_fixture", SCRIPT_DIR / "generate_batch_fixture.py"
)
_fixture = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_fixture)
DEFAULT_OUT = _fixture.DEFAULT_OUT
expected_counts = _fixture.expected_counts
generate = _fixture.generate
_find_source = _fixture._find_source


def _docker_mem_mb() -> float | None:
    try:
        out = subprocess.check_output(
            [
                "docker", "stats", "--no-stream",
                "--format", "{{.MemUsage}}",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            if "ttb" in line.lower() or "/" in line:
                part = line.split("/")[0].strip()
                if part.endswith("MiB"):
                    return float(part.replace("MiB", "").strip())
                if part.endswith("GiB"):
                    return float(part.replace("GiB", "").strip()) * 1024
        # docker stats without filter: pick first service line
        if out.strip():
            part = out.strip().split("\n")[0].split("/")[0].strip()
            if "MiB" in part:
                return float(part.replace("MiB", "").strip())
    except (subprocess.CalledProcessError, ValueError, IndexError):
        pass
    return None


def _docker_mem_for_ttb() -> float | None:
    try:
        out = subprocess.check_output(
            [
                "docker", "stats", "--no-stream",
                "--format", "{{.Name}}\t{{.MemUsage}}",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            if "ttb" not in line.lower():
                continue
            mem = line.split("\t", 1)[-1].split("/")[0].strip()
            if mem.endswith("MiB"):
                return float(mem.replace("MiB", "").strip())
            if mem.endswith("GiB"):
                return float(mem.replace("GiB", "").strip()) * 1024
    except (subprocess.CalledProcessError, ValueError, IndexError):
        pass
    return None


def _read_manifest_rows(manifest_path: Path) -> list[dict[str, str]]:
    import csv

    with manifest_path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _post_batch(
    base_url: str,
    image_paths: list[Path],
    manifest_path: Path,
    timeout_s: int,
) -> dict:
    files = []
    handles = []
    try:
        for p in image_paths:
            fh = p.open("rb")
            handles.append(fh)
            files.append(("images", (p.name, fh, "image/png")))
        mf = manifest_path.open("rb")
        handles.append(mf)
        files.append(("manifest", ("manifest.csv", mf, "text/csv")))
        files.append(("application", (None, "{}", "application/json")))

        r = requests.post(f"{base_url}/api/batch", files=files, timeout=timeout_s)
        r.raise_for_status()
        return r.json()
    finally:
        for h in handles:
            h.close()


def _merge_results(chunks: list[dict]) -> dict:
    all_results = []
    summary = {"total": 0, "pass": 0, "review": 0, "fail": 0, "processingTimeMs": 0}
    for chunk in chunks:
        all_results.extend(chunk.get("results") or [])
        s = chunk.get("summary") or {}
        for key in ("pass", "review", "fail"):
            summary[key] += int(s.get(key) or 0)
        summary["processingTimeMs"] += int(s.get("processingTimeMs") or 0)
    summary["total"] = len(all_results)
    return {"results": all_results, "summary": summary}


def run_test(
    base_url: str,
    fixture_dir: Path,
    count: int,
    chunk_size: int,
    timeout_s: int,
    docker: bool,
) -> dict:
    manifest_path = fixture_dir / "manifest.csv"
    if not manifest_path.is_file():
        source = _find_source(None)
        generate(count, fixture_dir, source)

    rows = _read_manifest_rows(manifest_path)
    if len(rows) < count:
        raise SystemExit(f"Manifest has {len(rows)} rows, need {count}")

    image_paths = [fixture_dir / f"label_{i:03d}.png" for i in range(1, count + 1)]
    missing = [p for p in image_paths if not p.is_file()]
    if missing:
        raise SystemExit(f"Missing {len(missing)} images under {fixture_dir}")

    mem_before = _docker_mem_for_ttb() if docker else None
    wall_start = time.perf_counter()
    chunks: list[dict] = []

    if chunk_size <= 0:
        data = _post_batch(base_url, image_paths, manifest_path, timeout_s)
        chunks.append(data)
    else:
        for start in range(0, count, chunk_size):
            end = min(start + chunk_size, count)
            chunk_paths = image_paths[start:end]
            chunk_rows = rows[start:end]
            chunk_manifest = fixture_dir / f"_chunk_manifest_{start:03d}.csv"
            import csv

            with chunk_manifest.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=chunk_rows[0].keys())
                writer.writeheader()
                writer.writerows(chunk_rows)
            print(f"Chunk {start // chunk_size + 1}: {len(chunk_paths)} images…")
            chunks.append(_post_batch(base_url, chunk_paths, chunk_manifest, timeout_s))

    wall_ms = int((time.perf_counter() - wall_start) * 1000)
    mem_after = _docker_mem_for_ttb() if docker else None
    merged = _merge_results(chunks) if len(chunks) > 1 else chunks[0]

    results = merged.get("results") or []
    summary = merged.get("summary") or {}
    filenames = {r.get("filename") for r in results}
    expected_files = {p.name for p in image_paths}

    errors = []
    if summary.get("total") != count:
        errors.append(f"summary.total={summary.get('total')} expected {count}")
    if len(results) != count:
        errors.append(f"len(results)={len(results)} expected {count}")
    if filenames != expected_files:
        missing_fn = expected_files - filenames
        extra_fn = filenames - expected_files
        if missing_fn:
            errors.append(f"missing filenames: {len(missing_fn)}")
        if extra_fn:
            errors.append(f"extra filenames: {len(extra_fn)}")

    exp = expected_counts(count)
    report = {
        "count": count,
        "chunkSize": chunk_size,
        "wallMs": wall_ms,
        "summary": summary,
        "expectedVerdicts": exp,
        "memBeforeMiB": mem_before,
        "memAfterMiB": mem_after,
        "errors": errors,
        "pass": not errors,
    }

    report_path = fixture_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch load test for /api/batch")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--chunk-size", type=int, default=0, help="0 = single request; e.g. 50 for chunked")
    parser.add_argument("--timeout", type=int, default=1200, help="HTTP timeout seconds")
    parser.add_argument("--docker", action="store_true", help="Record docker stats memory")
    args = parser.parse_args()

    mode = "single request" if args.chunk_size <= 0 else f"chunked ({args.chunk_size})"
    print(f"Batch load test: {args.count} images, {mode}")
    report = run_test(
        args.base_url,
        args.fixture_dir,
        args.count,
        args.chunk_size,
        args.timeout,
        args.docker,
    )

    print()
    print(f"Wall time: {report['wallMs']} ms ({report['wallMs'] / 1000:.1f} s)")
    print(f"Summary: {report['summary']}")
    if report.get("memBeforeMiB") is not None:
        print(f"Memory: {report['memBeforeMiB']:.0f} MiB -> {report['memAfterMiB']:.0f} MiB")
    print(f"Report: {args.fixture_dir / 'report.json'}")

    if report["errors"]:
        for e in report["errors"]:
            print(f"FAIL: {e}")
        return 1
    print("PASS: all assertions met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
