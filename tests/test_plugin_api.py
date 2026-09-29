"""Reader plugin API v1 contract tests (see docs/PLUGIN_API_v1.md).

Covers:
  (a) bundled example plugin satisfies the v1 contract;
  (b) minimal inline plugin (tmp dir + custom plugins-dir) converts 3 lines;
  (c) broken signature / missing Reader -> clear error at load (PluginError);
  (d) records() yields (lineno, bytes) in order.
"""

from __future__ import annotations

import inspect
import os
import struct
import tempfile
import unittest

import yaml

from core import plugins as plugins_mod
from core.plugins import PluginError
from core.schema import build_schema

TESTS_DIR = os.path.dirname(__file__)
PLUGINS_DIR = os.path.join(os.path.dirname(TESTS_DIR), "core", "plugin_examples")

FRAMED_SCHEMA = """\
format: framed
version: 1.0.0
parser: length_prefixed_frame
codepage: cp1252
fields:
  - {name: id,   start: 0,  length: 4}
  - {name: date, start: 4,  length: 8,  type: date,    format: YYYYMMDD}
  - {name: amt,  start: 12, length: 8,  type: decimal, scale: 2, align: right}
"""

MINIMAL_READER_SRC = """\
from collections.abc import Iterator

class Reader:
    def __init__(self, schema, path):
        self.schema = schema
        self.path = path

    def records(self, skip_first: bool = False) -> Iterator[tuple[int, bytes]]:
        with open(self.path, "rb") as fh:
            lineno = 0
            for raw in fh:
                record = raw.rstrip(b"\\r\\n")
                if not record:
                    continue
                lineno += 1
                if skip_first and lineno == 1:
                    continue
                yield lineno, record
"""

BROKEN_SIG_SRC = """\
class Reader:
    # Broken v1 signature: missing the mandatory (schema, path) params.
    def __init__(self, path):
        self.path = path

    def records(self, skip_first=False):
        yield 1, b"x"
"""

NO_READER_SRC = """\
# Broken plugin: no Reader class at all.
VALUE = 42
"""


def _framed(payload: bytes) -> bytes:
    return struct.pack(">H", len(payload)) + payload


def _payload(id_: str, date_: str, amt: str) -> bytes:
    return (id_[:4].ljust(4) + date_ + amt.rjust(8)).encode("ascii")


def _write(path: str, content: str | bytes) -> None:
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(content)


class TestPluginApiV1(unittest.TestCase):
    def test_a_example_plugin_satisfies_v1_contract(self):
        """Bundled length_prefixed_frame passes the frozen v1 contract."""
        schema = build_schema(yaml.safe_load(FRAMED_SCHEMA))
        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "input.bin")
            want = [
                _payload("0001", "20250115", "12345"),
                _payload("0002", "20250220", "67890"),
            ]
            with open(in_path, "wb") as fh:
                for p in want:
                    fh.write(_framed(p))
            reader = plugins_mod.load_reader(
                "length_prefixed_frame", schema, in_path, PLUGINS_DIR
            )
            # Class shape: Reader(schema, path) + records(skip_first).
            self.assertTrue(hasattr(reader, "records"))
            init_sig = inspect.signature(type(reader).__init__)
            params = [p for p in init_sig.parameters if p != "self"]
            self.assertEqual(params[:2], ["schema", "path"])
            rec_sig = inspect.signature(type(reader).records)
            rec_params = [p for p in rec_sig.parameters if p != "self"]
            self.assertIn("skip_first", rec_params)
            # Behaviour: yields (int, bytes) in order, raw payloads.
            got = list(reader.records())
            self.assertEqual([n for n, _ in got], [1, 2])
            for lineno, record in got:
                self.assertIsInstance(lineno, int)
                self.assertIsInstance(record, bytes)
            self.assertEqual([r for _, r in got], want)
            # skip_first skips frame 1 but keeps numbering of the rest.
            got_skip = list(reader.records(skip_first=True))
            self.assertEqual([r for _, r in got_skip], want[1:])
            self.assertEqual([n for n, _ in got_skip], [2])

    def test_b_minimal_inline_plugin_converts_three_lines(self):
        """Minimal inline plugin in a custom plugins-dir converts 3 lines."""
        schema = build_schema(
            {
                "format": "lines",
                "version": "1.0.0",
                "parser": "mini_lines",
                "fields": [{"name": "id", "length": 4}],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = os.path.join(tmp, "my_plugins")
            os.mkdir(plugins_dir)
            _write(os.path.join(plugins_dir, "mini_lines.py"), MINIMAL_READER_SRC)
            in_path = os.path.join(tmp, "input.txt")
            _write(in_path, b"aaa\nbbb\nccc\n")
            reader = plugins_mod.load_reader("mini_lines", schema, in_path, plugins_dir)
            got = list(reader.records())
            self.assertEqual(got, [(1, b"aaa"), (2, b"bbb"), (3, b"ccc")])

    def test_c_broken_signature_raises_clear_error_at_load(self):
        """Broken Reader signature / missing Reader -> clear load error."""
        schema = build_schema(
            {
                "format": "lines",
                "version": "1.0.0",
                "parser": "broken_sig",
                "fields": [{"name": "id", "length": 4}],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = os.path.join(tmp, "bad_plugins")
            os.mkdir(plugins_dir)
            _write(os.path.join(plugins_dir, "broken_sig.py"), BROKEN_SIG_SRC)
            _write(os.path.join(plugins_dir, "no_reader.py"), NO_READER_SRC)
            # Missing Reader class: strictly PluginError (frozen v1 rule).
            with self.assertRaises(PluginError) as ctx:
                plugins_mod.load_reader("no_reader", schema, "/tmp/x", plugins_dir)
            self.assertIn("no_reader", str(ctx.exception))
            self.assertIn("Reader", str(ctx.exception))
            # Wrong __init__ signature: contract violation. Frozen v1 says
            # loaders MUST surface it as PluginError with the original
            # TypeError as cause.
            with self.assertRaises(PluginError) as ctx2:
                plugins_mod.load_reader("broken_sig", schema, "/tmp/x", plugins_dir)
            self.assertIsInstance(ctx2.exception.__cause__, TypeError)
            self.assertIn("broken_sig", str(ctx2.exception))
            msg = str(ctx2.exception).lower()
            self.assertTrue(
                any(
                    token in msg
                    for token in (
                        "signature",
                        "__init__",
                        "positional",
                        "argument",
                        "reader",
                    )
                ),
                f"unclear load error message: {ctx2.exception!r}",
            )

    def test_d_records_yield_lineno_bytes_in_order(self):
        """records() yields (lineno:int, bytes) tuples, ordered 1..N."""
        schema = build_schema(
            {
                "format": "lines",
                "version": "1.0.0",
                "parser": "mini_lines",
                "fields": [{"name": "id", "length": 4}],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = os.path.join(tmp, "ordered_plugins")
            os.mkdir(plugins_dir)
            _write(os.path.join(plugins_dir, "mini_lines.py"), MINIMAL_READER_SRC)
            in_path = os.path.join(tmp, "input.txt")
            _write(in_path, b"one\ntwo\nthree\nfour\n")
            reader = plugins_mod.load_reader("mini_lines", schema, in_path, plugins_dir)
            got = list(reader.records())
            self.assertEqual(len(got), 4)
            linenos = [n for n, _ in got]
            self.assertEqual(linenos, [1, 2, 3, 4])
            self.assertEqual(linenos, sorted(linenos))
            for lineno, record in got:
                self.assertIsInstance(lineno, int)
                self.assertIsInstance(record, bytes)
            self.assertEqual([r for _, r in got], [b"one", b"two", b"three", b"four"])


if __name__ == "__main__":
    unittest.main()
