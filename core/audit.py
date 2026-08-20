"""Audit evidence: SHA-256 hashes + conversion summary sidecar.

For regulated environments (banking, health) the sidecar answers: what was
converted, when, and with which schema version.
"""

from __future__ import annotations

import datetime
import hashlib

from .rules import RuleViolation
from .schema import Schema
from .validator import Stats


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_summary(
    input_path: str,
    output_path: str,
    schema: Schema,
    stats: Stats,
    violations: list[RuleViolation],
) -> list[str]:
    lines = [f"input_sha256={sha256_file(input_path)}"]
    if output_path != "-":
        try:
            lines.append(f"output_sha256={sha256_file(output_path)}")
        except OSError:
            lines.append("output_sha256=unavailable")
    lines += [
        f"schema_format={schema.format}",
        f"schema_version={schema.version}",
        f"records_total={stats.total}",
        f"records_ok={stats.ok}",
        f"records_errors={stats.errors}",
        f"rule_violations={len(violations)}",
        f"utc_timestamp={datetime.datetime.now(datetime.timezone.utc).isoformat()}",
    ]
    return lines
