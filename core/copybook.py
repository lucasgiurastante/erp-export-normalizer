"""COBOL copybook reader: FD/PIC clauses -> erp-normalize schema.

Mainframe teams describe their flat files with copybooks (`FD` / `01` / `05`
levels and `PIC` clauses). This module turns such a copybook into the YAML
schema the rest of the tool consumes, so the record layout lives in the
client's own COBOL source instead of being retyped by hand.

Supported (the subset that covers real sequential exports):

    PIC X(n)                     -> string, n bytes
    PIC 9(n)                     -> decimal, n bytes
    PIC S9(n)V99                 -> packed (COMP-3 when the clause says so)
    PIC 9(8)                     -> date YYYYMMDD when it is the only 8-digit
                                    numeric field and a date is requested
    PIC S9(n)V9(m) COMP-3        -> packed with scale m
    PIC S9(n)V9(m) COMP/BINARY   -> binary, decoded as big-endian two's
                                    complement (see BINARY_* below)

Levels: only leaf items (those carrying a `PIC`) become fields. The record
starts at the first `01` of the selected FD and every subsequent `01` starts
a new record, so `record_length` is the width of the first one.
"""

from __future__ import annotations

import os
import re

# COBOL is fixed-format: columns 1-6 sequence, 7 indicator, 8+ code.
# Strip the sequence area and comment lines, then normalise whitespace.
_SEQUENCE_RE = re.compile(r"^.{6}")
_COMMENT_RE = re.compile(r"^\s*[*]/", re.IGNORECASE)

# "05  CUST-ID       PIC X(6)."  /  "05 CUST-BAL PIC S9(7)V99 COMP-3."
_ITEM_RE = re.compile(
    r"""^
    \s*(?P<level>\d{2})\s+                # 01 / 05 / 10 level number
    (?P<name>[A-Z0-9][A-Z0-9-]*)\s+      # data name
    (?:(?:REDEFINES|OCCURS\s+\d+(?:\s+TO\s+\d+)?)\s+)*   # skip noise clauses
    (?:PIC|PICTURE)\s+(?P<pic>\S+)
    (?P<rest>.*)
    \.?$
    """,
    re.VERBOSE | re.IGNORECASE,
)

_FD_RE = re.compile(r"^\s*FD\s+(?P<name>[A-Z0-9][A-Z0-9-]*)", re.IGNORECASE)
_RECORD_01_RE = re.compile(r"^\s*01\s+(?P<name>[A-Z0-9][A-Z0-9-]*)")

# 9(7)V99 -> 7 integer digits, 2 decimals ; X(20) -> 20 chars
# 8-byte signed binary holds up to 18 digits, well past a 9(15) COMP-5.
BINARY_WIDTHS = {2: 4, 4: 9, 8: 18}

DEFAULT_CODEPAGE = "ebcdic-cp037"

PIC_TYPE_CHARS = frozenset("XAN9")

PACKED_USAGES = {"COMP-3", "PACKED-DECIMAL"}
BINARY_USAGES = {"COMP", "COMP-4", "COMPUTATIONAL", "COMPUTATIONAL-4", "BINARY"}


class CopybookError(ValueError):
    """The copybook could not be parsed into a schema."""


class CopybookField:
    """One leaf item resolved to byte offsets and a schema field type."""

    def __init__(
        self,
        name: str,
        start: int,
        length: int,
        ftype: str = "string",
        scale: int = 0,
        date_format: str | None = None,
    ):
        self.name = name
        self.start = start
        self.length = length
        self.type = ftype
        self.scale = scale
        self.date_format = date_format

    def to_schema(self) -> dict:
        out: dict = {
            "name": self.name,
            "start": self.start,
            "length": self.length,
        }
        if self.type != "string":
            out["type"] = self.type
        if self.type == "date":
            out["format"] = self.date_format or "YYYYMMDD"
        elif self.type == "packed" or (self.type == "decimal" and self.scale):
            out["scale"] = self.scale
        return out


def normalize_field_name(raw: str) -> str:
    """COBOL data names are uppercase with dashes; YAML/SQL keys prefer `_`."""
    return raw.strip().upper().replace("-", "_")


def _decode_usages(rest: str) -> set[str]:
    """USAGE / COMP words, with the sentence period dropped."""
    out = set()
    for token in rest.split():
        cleaned = token.strip(".,;()").upper()
        if cleaned:
            out.add(cleaned)
    return out


def parse_picture(pic: str) -> tuple[str, int, int, int]:
    """Resolve one PIC clause into `(kind, length, scale, digits)`.

    A picture is a run of typed positions. Each `X`, `A` or `9` is one
    position; only the `(n)` form repeats it. `V` inserts the implied decimal
    point, so the positions *after* it are the decimals:

        PIC X(6)        -> ("string",  6, 0, 0)
        PIC 9(8)        -> ("numeric", 8, 0, 8)
        PIC S9(7)V99    -> ("numeric", 9, 2, 9)   # 7 integer + 2 decimals
        PIC S9(5)V9(4)  -> ("numeric", 9, 4, 9)
    """
    text = pic.strip().rstrip(".").upper()
    if not text:
        raise CopybookError("empty PIC clause")
    if text.startswith("S"):
        text = text[1:]  # a leading sign occupies no position

    vpos = text.find("V")
    integer = _positions(text if vpos == -1 else text[:vpos], pic)
    decimals = 0 if vpos == -1 else _positions(text[vpos + 1 :], pic)
    alnum = _alnum_positions(text.replace("V", ""), pic)

    if alnum:
        if vpos != -1:
            raise CopybookError(f"V in an alphanumeric PIC clause: {pic!r}")
        return "string", integer, 0, 0
    if integer + decimals == 0:
        raise CopybookError(f"PIC clause without size: {pic!r}")
    return "numeric", integer + decimals, decimals, integer + decimals


def _walk(body: str, pic: str):
    """Yield `(char, count)` for every position group in a picture fragment."""
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch not in PIC_TYPE_CHARS:
            raise CopybookError(f"unsupported character {ch!r} in PIC clause {pic!r}")
        i += 1
        if i < n and body[i] == "(":
            end = body.find(")", i)
            if end == -1:
                raise CopybookError(f"unbalanced '(' in PIC clause: {pic!r}")
            yield ch, _parse_count(body[i + 1 : end], pic)
            i = end + 1
        else:
            yield ch, 1


def _positions(body: str, pic: str) -> int:
    """Total positions in a picture fragment, honouring `(n)` repeats."""
    return sum(count for _, count in _walk(body, pic))


def _alnum_positions(body: str, pic: str) -> int:
    """Positions typed `X` or `A` (0 when the picture is purely numeric)."""
    return sum(count for ch, count in _walk(body, pic) if ch in "XA")


def _parse_count(raw: str, pic: str) -> int:
    if not raw.isdigit():
        raise CopybookError(f"invalid repeat count {raw!r} in PIC clause {pic!r}")
    value = int(raw)
    if value <= 0:
        raise CopybookError(f"repeat count must be > 0 in PIC clause {pic!r}")
    return value


def _field_for(
    raw_name: str,
    pic: str,
    usages: set[str],
    start: int,
    want_date: bool,
) -> CopybookField:
    kind, length, scale, digits = parse_picture(pic)
    name = normalize_field_name(raw_name)
    if kind == "string":
        if "COMP" in " ".join(usages) and usages & (PACKED_USAGES | BINARY_USAGES):
            raise CopybookError(
                f"field {raw_name!r}: USAGE COMP/PACKED on an alphanumeric PIC"
            )
        return CopybookField(name, start, length, "string")

    if usages & PACKED_USAGES:
        # COMP-3 packs two digits per byte plus a sign nibble.
        byte_len = (digits + 2) // 2
        return CopybookField(name, start, byte_len, "packed", scale=scale)

    if usages & BINARY_USAGES:
        # Binary is stored as a full machine word, not packed.
        byte_len = BINARY_WIDTHS.get(digits, 2 if digits <= 4 else 4)
        return CopybookField(name, start, byte_len, "decimal", scale=scale)

    if want_date and digits == 8 and scale == 0:
        return CopybookField(name, start, length, "date", date_format="YYYYMMDD")

    return CopybookField(name, start, length, "decimal", scale=scale)


def _logical_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.read().splitlines()
    out: list[str] = []
    for line in raw_lines:
        line = _SEQUENCE_RE.sub("", line, count=1)
        line = _COMMENT_RE.sub("", line)
        if not line.strip():
            continue
        if line[:1] in ("*", "/"):
            continue
        if not line.strip().rstrip("."):
            continue
        out.append(line.rstrip())
    return out


def parse_copybook(
    path: str,
    record: str | None = None,
    table: str | None = None,
    codepage: str = DEFAULT_CODEPAGE,
    want_date: bool = True,
) -> dict:
    """Parse a copybook and return a schema dict for `build_schema`.

    `record` selects the `01` record name when the copybook declares several;
    by default the first one is used.
    """
    lines = _logical_lines(path)

    record_name: str | None = None
    wanted = (record or "").strip().upper() or None
    selected = wanted is None

    fields: list[CopybookField] = []
    offset = 0
    pending: list[tuple[str, str, set[str]]] = []
    in_selected_fd = False
    saw_fd = False

    for line in lines:
        fd_match = _FD_RE.match(line)
        if fd_match:
            # A new FD starts a fresh record area. When a record name was
            # requested we cannot know yet which 01 will match, so every FD
            # is scanned until the named 01 shows up.
            saw_fd = True
            record_name = None
            selected = wanted is None
            fields = []
            offset = 0
            pending = []
            in_selected_fd = True
            continue

        if not in_selected_fd:
            continue

        rec_match = _RECORD_01_RE.match(line)
        if rec_match:
            name = rec_match.group("name").upper()
            if not selected:
                if name == wanted:
                    selected = True
                    record_name = name
                    fields = []
                    offset = 0
                    pending = []
                continue
            if record_name is not None:
                break  # a second 01 closes the record we were building
            record_name = name
            fields = []
            offset = 0
            pending = []
            continue

        if not selected:
            continue

        item = _ITEM_RE.match(line)
        if not item:
            continue
        for rname, rpic, rusages in pending:
            fld = _field_for(rname, rpic, rusages, offset, want_date)
            fields.append(fld)
            offset += fld.length
        pending = []
        pending.append(
            (
                item.group("name"),
                item.group("pic").rstrip("."),
                _decode_usages(item.group("rest")),
            )
        )

    if pending:
        for rname, rpic, rusages in pending:
            fld = _field_for(rname, rpic, rusages, offset, want_date)
            fields.append(fld)
            offset += fld.length

    if not saw_fd:
        raise CopybookError(f"{path}: no FD declaration found")
    if not fields:
        raise CopybookError(
            f"{path}: no record found"
            + (f" named {wanted!r}" if wanted else "")
            + " (need an FD with a 01 record of PIC fields)"
        )

    return {
        "format": "cobol_copybook",
        "version": "1.0.0",
        "description": f"Imported from COBOL copybook {os.path.basename(path)}",
        "record_length": offset,
        "codepage": codepage,
        "table": table,
        "fields": [f.to_schema() for f in fields],
    }
