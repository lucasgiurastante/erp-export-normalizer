"""Transparent decompression of input files.

Server-to-server transfers almost always arrive compressed, and a `.gz` that
the tool reads as raw bytes produces a wall of nonsense field errors that
says nothing useful:

    line 1: field 'date': invalid date '00T\\x00\\x02O┐0'

The container is detected from its **magic bytes**, never from the file
extension. An extension is a claim by whoever renamed the file; the first
bytes are what the file actually is, and a client that ships `data.txt.gz`
containing uncompressed data would be rejected by extension sniffing.

`gzip` and `bzip2` are handled with the stdlib. ZIP is not: a zip holds
several named members, and silently picking the first one is a guess about
which export the caller meant. It is detected and refused with a message
that says what to do instead.
"""

from __future__ import annotations

import bz2
import gzip
import io
import os
from collections.abc import Callable, Iterator
from typing import BinaryIO, cast

GZIP_MAGIC = b"\x1f\x8b"
ZIP_MAGIC = b"PK\x03\x04"
BZIP2_MAGIC = b"BZh"

# Sniffing only needs a few bytes; reading a whole file to find out would
# defeat the point of streaming.
SNIFF_BYTES = 4


class DecompressionError(ValueError):
    """The container is known but cannot be read."""


class ZipUnsupported(DecompressionError):
    """A zip archive: real, but ambiguous, and refused on purpose."""


def sniff(path: str) -> str | None:
    """Return the container name by looking at the first bytes, or None.

    A missing file stays an `OSError`: that is a clearer signal than a
    decompression complaint, and callers already handle it as I/O.
    """
    with open(path, "rb") as fh:
        head = fh.read(SNIFF_BYTES)
    if head.startswith(GZIP_MAGIC):
        return "gzip"
    if head.startswith(ZIP_MAGIC):
        return "zip"
    if head.startswith(BZIP2_MAGIC):
        return "bzip2"
    return None


def open_binary(path: str) -> BinaryIO:
    """Open `path` for binary reading, decompressing known containers.

    Always returns a binary stream in *record* space, so the readers keep
    working unchanged.
    """
    kind = sniff(path)
    if kind is None:
        return open(path, "rb")
    if kind == "zip":
        raise ZipUnsupported(
            f"{os.path.basename(path)} is a zip archive. A zip can hold "
            "several files and we would have to guess which one is the "
            "export. Extract the member first, or gzip it on the sending "
            "side: gzip -c export.txt > export.txt.gz"
        )
    opener = cast("Callable[..., BinaryIO]", gzip.open if kind == "gzip" else bz2.open)
    try:
        return opener(path, "rb")
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise DecompressionError(
            f"{os.path.basename(path)} looks like {kind} but cannot be "
            f"decompressed ({exc}); the file is truncated or corrupt"
        ) from exc


def iter_lines(stream: BinaryIO) -> Iterator[tuple[int, bytes]]:
    """Shared record iteration, so every reader benefits from the same rules.

    A truncated container only fails when the read runs off the end of the
    stream, well after `open()` succeeded, so the error is translated here
    rather than leaking an `EOFError` from the stdlib to the caller.
    """
    try:
        for lineno, raw in enumerate(stream, start=1):
            record = raw.rstrip(b"\r\n")
            if not record:
                continue
            yield lineno, record
    except (EOFError, gzip.BadGzipFile, OSError) as exc:
        name = getattr(stream, "name", "input")
        raise DecompressionError(
            f"{os.path.basename(name)} ended before the compressed stream "
            f"was complete ({exc}); the file is truncated or corrupt"
        ) from exc


def peek_text_sample(path: str, limit: int = 512) -> bytes:
    """A few decompressed bytes, for detectors that guess from a sample."""
    with open_binary(path) as fh:
        return fh.read(limit)


__all__ = [
    "DecompressionError",
    "ZipUnsupported",
    "iter_lines",
    "open_binary",
    "peek_text_sample",
    "sniff",
    "io",
]
