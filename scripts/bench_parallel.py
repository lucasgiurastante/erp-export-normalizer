"""Benchmark parallel vs serial validation (P0-5).

Builds a synthetic input by repeating the real ``jde_ar`` fixed-width
fixture, then converts it with workers 1 vs 2/4 x chunk 10k/100k.
Prints wall time + md5 per config; all md5 must be identical
(deterministic ordering).

Outputs go to /tmp (never committed). Example:

    ./.venv/bin/python scripts/bench_parallel.py
    ./.venv/bin/python scripts/bench_parallel.py --lines 50000 --workers 1 2 --chunks 10000 100000
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from cli import main  # noqa: E402

SCHEMA_PATH = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
_FIXTURE_CANDIDATES = (
    os.path.join(REPO_ROOT, "examples", "jde_ar.txt"),
    os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt"),
)
FIXTURE_PATH = next(p for p in _FIXTURE_CANDIDATES if os.path.exists(p))


def _md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_input(lines: int) -> str:
    with open(FIXTURE_PATH, "rb") as fh:
        seed = fh.read().splitlines(keepends=True)
    if not seed:
        raise RuntimeError(f"empty fixture: {FIXTURE_PATH}")
    fd, path = tempfile.mkstemp(prefix="bench_parallel_in_", suffix=".txt", dir="/tmp")
    with os.fdopen(fd, "wb") as fh:
        for i in range(lines):
            fh.write(seed[i % len(seed)])
    return path


def _run_once(input_path: str, workers: int, chunk_lines: int) -> tuple[float, str]:
    fd, out_path = tempfile.mkstemp(
        prefix=f"bench_parallel_w{workers}_c{chunk_lines}_", suffix=".json", dir="/tmp"
    )
    os.close(fd)
    argv = [
        "--schema", SCHEMA_PATH,
        "--input", input_path,
        "--output", out_path,
        "--format", "json",
        "--workers", str(workers),
        "--chunk-lines", str(chunk_lines),
    ]
    start = time.perf_counter()
    code = main(argv)
    elapsed = time.perf_counter() - start
    if code != 0:
        raise RuntimeError(f"conversion failed (exit {code}): workers={workers} chunk={chunk_lines}")
    digest = _md5(out_path)
    os.unlink(out_path)
    return elapsed, digest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lines", type=int, default=50_000)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    ap.add_argument("--chunks", type=int, nargs="+", default=[10_000, 100_000])
    return ap.parse_args(argv)


def main_bench(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = _build_input(args.lines)
    print(f"fixture : {FIXTURE_PATH}")
    print(f"input   : {args.lines} lines ({os.path.getsize(input_path)} bytes)")
    print(f"schema  : {SCHEMA_PATH}")
    print()
    print(f"{'workers':>7} {'chunk':>8} {'seconds':>9} md5")
    results: list[tuple[int, int, float, str]] = []
    try:
        for workers in args.workers:
            for chunk in args.chunks:
                # Serial path ignores --chunk-lines; run it once per worker=1 row.
                if workers == 1 and chunk != args.chunks[0]:
                    continue
                elapsed, digest = _run_once(input_path, workers, chunk)
                results.append((workers, chunk, elapsed, digest))
                print(f"{workers:>7} {chunk:>8} {elapsed:>9.2f} {digest}")
    finally:
        os.unlink(input_path)
    print()
    digests = {d for _, _, _, d in results}
    if len(digests) == 1:
        print(f"OK: all {len(results)} outputs byte-identical (md5 {digests.pop()})")
        return 0
    print(f"MISMATCH: {len(digests)} distinct md5: {sorted(digests)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main_bench())
