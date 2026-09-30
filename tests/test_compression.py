"""Transparent decompression: gzip and bzip2 in, same output out."""

from __future__ import annotations

import bz2
import contextlib
import gzip
import io
import os
import tempfile
import unittest
import zipfile

from cli import main
from core import compression
from core.compression import DecompressionError, ZipUnsupported, sniff

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")

PLAIN_MD5 = "a1b6de73df4cd03264053c7d7b70cee7"


def write_gz(tmp: str, source: str = FIXTURE) -> str:
    path = os.path.join(tmp, "export.txt.gz")
    with open(source, "rb") as src, gzip.open(path, "wb") as dst:
        dst.write(src.read())
    return path


class TestSniff(unittest.TestCase):
    def test_detects_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sniff(write_gz(tmp)), "gzip")

    def test_detects_bzip2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.bz2")
            with open(FIXTURE, "rb") as fh:
                data = fh.read()
            with open(path, "wb") as fh:
                fh.write(bz2.compress(data))
            self.assertEqual(sniff(path), "bzip2")

    def test_detects_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "z.zip")
            with zipfile.ZipFile(path, "w") as zf:
                zf.write(FIXTURE, "export.txt")
            self.assertEqual(sniff(path), "zip")

    def test_plain_file_is_none(self):
        self.assertIsNone(sniff(FIXTURE))

    def test_extension_does_not_matter(self):
        """A gzip named .txt is a gzip. The extension is a claim, not a fact."""
        with tempfile.TemporaryDirectory() as tmp:
            src = write_gz(tmp)
            lying = os.path.join(tmp, "looks_plain.txt")
            with open(src, "rb") as a, open(lying, "wb") as b:
                b.write(a.read())
            self.assertEqual(sniff(lying), "gzip")

    def test_empty_file_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.txt")
            open(path, "wb").close()
            self.assertIsNone(sniff(path))


class TestZipRefused(unittest.TestCase):
    def test_zip_raises_with_advice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "z.zip")
            with zipfile.ZipFile(path, "w") as zf:
                zf.write(FIXTURE, "export.txt")
            with self.assertRaises(ZipUnsupported) as ctx:
                compression.open_binary(path)
            message = str(ctx.exception)
            self.assertIn("zip", message)
            self.assertIn("gzip -c", message)


class TestOpenBinary(unittest.TestCase):
    def test_plain_passes_through(self):
        with compression.open_binary(FIXTURE) as fh:
            first = fh.readline().rstrip(b"\r\n")
        self.assertEqual(len(first), 44)

    def test_gzip_is_decompressed(self):
        with tempfile.TemporaryDirectory() as tmp, open(FIXTURE, "rb") as plain:
            expected = plain.read()
            with compression.open_binary(write_gz(tmp)) as fh:
                self.assertEqual(fh.read(), expected)

    def test_truncated_gzip_raises_while_iterating(self):
        """Opening succeeds; only reading finds out.

        The translation lives in `iter_lines` because that is where the read
        actually happens, so a caller that reads the stream directly still
        sees the stdlib error. The readers all go through `iter_lines`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            full = write_gz(tmp)
            broken = os.path.join(tmp, "broken.gz")
            with open(full, "rb") as fh:
                head = fh.read(30)
            with open(broken, "wb") as fh:
                fh.write(head)
            with (
                self.assertRaises(DecompressionError) as ctx,
                compression.open_binary(broken) as fh,
            ):
                list(compression.iter_lines(fh))
            self.assertIn("truncated or corrupt", str(ctx.exception))


class TestConversionIsIdentical(unittest.TestCase):
    def _convert(self, src: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o.json")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        src,
                        "--output",
                        out,
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                return fh.read()

    def test_gzip_output_matches_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._convert(write_gz(tmp)), self._convert(FIXTURE))

    def test_bzip2_output_matches_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "export.bz2")
            with open(FIXTURE, "rb") as fh:
                data = fh.read()
            with open(path, "wb") as fh:
                fh.write(bz2.compress(data))
            self.assertEqual(self._convert(path), self._convert(FIXTURE))

    def test_lies_about_its_extension_and_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            gz = write_gz(tmp)
            lying = os.path.join(tmp, "export.txt")
            with open(gz, "rb") as a, open(lying, "wb") as b:
                b.write(a.read())
            self.assertEqual(self._convert(lying), self._convert(FIXTURE))


class TestAutoDetection(unittest.TestCase):
    def test_detector_reads_a_gzip(self):
        """Without a schema, auto-detection has to see the real records."""
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(out),
            ):
                code = main(
                    ["--input", write_gz(tmp), "--output", "-", "--format", "json"]
                )
            self.assertEqual(code, 0)
            self.assertIn("jde_ar", out.getvalue())

    def test_generator_reads_a_gzip(self):
        """The generator infers *delimited* schemas, so give it a CSV."""
        from core import generator

        with tempfile.TemporaryDirectory() as tmp:
            plain = os.path.join(tmp, "data.csv")
            with open(plain, "wb") as fh:
                fh.write(b"id,amount,label\na,10.50,x\nb,20.25,y\nc,5.00,z\n")
            gz = os.path.join(tmp, "data.csv.gz")
            with open(plain, "rb") as src, gzip.open(gz, "wb") as dst:
                dst.write(src.read())
            data = generator.generate_schema(gz)
            self.assertEqual(len(data["fields"]), 3)


class TestErrorsReachTheUser(unittest.TestCase):
    def test_zip_returns_a_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = os.path.join(tmp, "z.zip")
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.write(FIXTURE, "export.txt")
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        zpath,
                        "--output",
                        os.path.join(tmp, "o.json"),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("zip archive", err.getvalue())

    def test_truncated_gzip_returns_a_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = write_gz(tmp)
            broken = os.path.join(tmp, "broken.gz")
            with open(full, "rb") as fh:
                head = fh.read(30)
            with open(broken, "wb") as fh:
                fh.write(head)
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        broken,
                        "--output",
                        os.path.join(tmp, "o.json"),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("truncated or corrupt", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
