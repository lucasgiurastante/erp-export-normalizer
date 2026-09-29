"""CI performance profile: machine-readable timings + a regression gate.

`bench_parallel.py` answers "does parallel beat serial, and is the output
still identical?". This script answers the question CI actually needs:
*did this change make the converter slower than it was?*

Design notes:

- Timings go to a JSON report (`--output`) so CI can diff two runs instead of
  scraping a table.
- The gate is a **relative** threshold against a committed baseline
  (`--baseline`), never an absolute wall-clock number: shared runners are
  noisy, and a hard-coded limit produces flaky failures nobody trusts.
- The determinism check is absolute and never relaxes: every configuration
  must produce byte-identical output. That is the product promise, not a
  performance property.
- Exit 0 clean, 1 regression or mismatch, 2 bad usage.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from cli import main as convert_main  # noqa: E402

SCHEMA_PATH = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
_FIXTURE_CANDIDATES = (
    os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt"),
    os.path.join(REPO_ROOT, "examples", "jde_ar.txt"),
)
FIXTURE_PATH = next(p for p in _FIXTURE_CANDIDATES if os.path.exists(p))

# A shared runner is noisy; only a regression this large is treated as real.
DEFAULT_TOLERANCE = 0.50


def _build_input(lines: int) -> str:
    with open(FIXTURE_PATH, "rb") as fh:
        seed = fh.read().splitlines(keepends=True)
    if not seed:
        raise RuntimeError(f"empty fixture: {FIXTURE_PATH}")
    fd, path = tempfile.mkstemp(prefix="perf_in_", suffix=".txt")
    with os.fdopen(fd, "wb") as fh:
        for i in range(lines):
            fh.write(seed[i % len(seed)])
    return path


def _digest(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(input_path: str, workers: int, chunk_lines: int) -> dict:
    fd, out_path = tempfile.mkstemp(prefix="perf_out_", suffix=".json")
    os.close(fd)
    try:
        # The CLI prints its record summary to stderr; keep it out of the
        # report so the JSON stays machine readable.
        start = time.perf_counter()
        with contextlib.redirect_stderr(io.StringIO()):
            code = convert_main(
                [
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    input_path,
                    "--output",
                    out_path,
                    "--format",
                    "json",
                    "--workers",
                    str(workers),
                    "--chunk-lines",
                    str(chunk_lines),
                ]
            )
        elapsed = time.perf_counter() - start
        if code != 0:
            raise RuntimeError(f"conversion failed (exit {code})")
        size = os.path.getsize(out_path)
        rows = None
        with open(out_path, encoding="utf-8") as fh:
            # cheap count: the writer streams one object per record
            rows = fh.read().count('"id"')
        return {
            "workers": workers,
            "chunk_lines": chunk_lines,
            "seconds": round(elapsed, 4),
            "rows": rows,
            "bytes": size,
            "md5": _digest(out_path),
        }
    finally:
        os.unlink(out_path)


def _case_key(row: dict) -> str:
    return f"w{row['workers']}c{row['chunk_lines']}"


def compare(current: list[dict], baseline: list[dict], tolerance: float) -> list[dict]:
    """Return one verdict per case that has a baseline entry."""
    base_by_key = {_case_key(r): r for r in baseline}
    out: list[dict] = []
    for row in current:
        key = _case_key(row)
        ref = base_by_key.get(key)
        if ref is None or not ref.get("seconds"):
            out.append({"case": key, "status": "new", "ratio": None})
            continue
        ratio = row["seconds"] / ref["seconds"]
        if ratio > 1 + tolerance:
            out.append(
                {
                    "case": key,
                    "status": "regression",
                    "ratio": round(ratio, 3),
                    "now": row["seconds"],
                    "baseline": ref["seconds"],
                }
            )
        else:
            out.append(
                {
                    "case": key,
                    "status": "ok",
                    "ratio": round(ratio, 3),
                    "now": row["seconds"],
                    "baseline": ref["seconds"],
                }
            )
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lines", type=int, default=50_000)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    ap.add_argument("--chunks", type=int, nargs="+", default=[10_000, 100_000])
    ap.add_argument(
        "--baseline",
        help="previous report JSON to compare against (relative gate)",
    )
    ap.add_argument(
        "--output", help="write the report JSON here (default: stdout only)"
    )
    ap.add_argument(
        "--update-baseline",
        metavar="PATH",
        help="write this run as the new baseline file, then exit",
    )
    ap.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"allowed slowdown vs baseline (default {DEFAULT_TOLERANCE:.0%})",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="runs per case; the fastest is kept (default 1)",
    )
    return ap.parse_args(argv)


def _run_best(input_path: str, workers: int, chunk: int, repeat: int) -> dict:
    best = None
    for _ in range(max(1, repeat)):
        row = _run(input_path, workers, chunk)
        if best is None or row["seconds"] < best["seconds"]:
            best = row
    assert best is not None
    return best


def main_perf(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.tolerance < 0:
        print("perf error: --tolerance must be >= 0", file=sys.stderr)
        return 2

    input_path = _build_input(args.lines)
    results: list[dict] = []
    try:
        for workers in args.workers:
            for chunk in args.chunks:
                if workers == 1 and chunk != args.chunks[0]:
                    continue  # the serial path ignores --chunk-lines
                results.append(_run_best(input_path, workers, chunk, args.repeat))
    except (OSError, RuntimeError) as exc:
        os.unlink(input_path)
        print(f"perf error: {exc}", file=sys.stderr)
        return 2

    digests = {r["md5"] for r in results}
    serial = next((r for r in results if r["workers"] == 1), None)
    parallel = [r for r in results if r["workers"] > 1]
    report = {
        "lines": args.lines,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cases": results,
        "identical_outputs": len(digests) == 1,
        "md5": sorted(digests),
        "speedup_vs_serial": (
            {
                f"w{r['workers']}": round(serial["seconds"] / r["seconds"], 2)
                for r in parallel
            }
            if serial and serial["seconds"]
            else {}
        ),
    }

    payload = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    if args.update_baseline:
        with open(args.update_baseline, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"baseline written: {args.update_baseline}")
        return 0

    print(f"{'case':>12} {'rows':>8} {'seconds':>9}")
    for row in results:
        print(f"{_case_key(row):>12} {row['rows']:>8} {row['seconds']:>9.3f}")

    # Determinism is absolute: it is the product promise.
    if len(digests) != 1:
        print(
            f"\nFAIL: {len(digests)} distinct outputs across "
            f"{len(results)} configs: {sorted(digests)}",
            file=sys.stderr,
        )
        return 1

    if not args.baseline:
        print(f"\nno baseline given; timings recorded, md5 identical ({digests.pop()})")
        return 0
    with open(args.baseline, encoding="utf-8") as fh:
        baseline = json.load(fh)
    verdicts = compare(results, baseline.get("cases", []), args.tolerance)
    print()
    for v in verdicts:
        if v["status"] == "regression":
            print(
                f"REGRESSION {v['case']}: {v['now']}s vs baseline "
                f"{v['baseline']}s ({v['ratio']}x, limit "
                f"{1 + args.tolerance:.2f}x)",
                file=sys.stderr,
            )
        else:
            detail = f"{v['ratio']}x" if v["ratio"] else "no baseline"
            print(f"  {v['status']:>5} {v['case']}: {detail}")

    if any(v["status"] == "regression" for v in verdicts):
        return 1
    print(f"\nOK: no regression beyond {args.tolerance:.0%}, md5 identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_perf())
