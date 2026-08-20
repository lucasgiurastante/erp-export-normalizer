"""Chunked parallel validation with deterministic ordering.

Records are validated in fixed-size chunks by a process pool; results are
yielded back in input order, so `--workers N` produces byte-identical
output to `--workers 1`.
"""
from __future__ import annotations

from collections.abc import Iterator

from .schema import Schema
from .validator import RecordResult, Validator

CHUNK_LINES = 100_000


def _validate_chunk(
    schema: Schema, chunk: list[tuple[int, bytes]]
) -> list[RecordResult]:
    val = Validator(schema)
    return [val.validate_record(lineno, record) for lineno, record in chunk]


def validate_parallel(
    records: Iterator[tuple[int, bytes]],
    schema: Schema,
    workers: int,
    chunk_lines: int = CHUNK_LINES,
) -> Iterator[RecordResult]:
    """Validate `records` with a process pool; yield in input order."""
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers) as executor:
        pending = []
        chunk: list[tuple[int, bytes]] = []
        for item in records:
            chunk.append(item)
            if len(chunk) >= chunk_lines:
                pending.append(executor.submit(_validate_chunk, schema, chunk))
                chunk = []
        if chunk:
            pending.append(executor.submit(_validate_chunk, schema, chunk))
        for future in pending:
            yield from future.result()