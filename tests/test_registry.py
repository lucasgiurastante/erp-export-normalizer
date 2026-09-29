"""Versioned schema registry: index format, search and verification."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest

import yaml

from cli import main
from core import registry
from core.registry import RegistryError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO_ROOT, "registry.yaml")
SCHEMA_SRC = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")

VALID_SCHEMA = """\
format: t
version: 1.0.0
record_length: 4
fields:
  - {name: id, start: 0, length: 4}
"""


def write_index(tmp: str, schemas: list[dict], **overrides) -> str:
    doc = {"registry": 1, "updated": "2026-09-29", "schemas": schemas}
    doc.update(overrides)
    path = os.path.join(tmp, "registry.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
    return path


def write_schema(tmp: str, name: str = "s.yaml", text: str = VALID_SCHEMA) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def entry(full_path: str, **kwargs) -> dict:
    """A registry entry pointing at `full_path`, with its real checksum.

    The stored `path` is relative to the index, which lives in the same
    directory, so `registry.resolve()` finds it.
    """
    row = {
        "name": "t",
        "path": os.path.basename(full_path),
        "version": "1.0.0",
        "sha256": registry.sha256_file(full_path),
    }
    row.update(kwargs)
    return row


class TestShippedIndex(unittest.TestCase):
    """The repo's own index must be self-consistent, or trust is theatre."""

    def test_loads(self):
        entries = registry.load_index(INDEX)
        self.assertTrue(entries)

    def test_every_entry_verifies(self):
        report = registry.verify(registry.load_index(INDEX))
        bad = [r for r in report if not r["ok"]]
        self.assertEqual(bad, [], f"index entries failed: {bad}")

    def test_covers_every_built_in_schema(self):
        names = {e.name for e in registry.load_index(INDEX)}
        on_disk = {
            os.path.splitext(f)[0]
            for f in os.listdir(os.path.join(REPO_ROOT, "core", "formats"))
            if f.endswith(".yaml")
        }
        self.assertEqual(on_disk - names, set(), "a built-in schema is not indexed")


class TestIndexFormat(unittest.TestCase):
    def test_missing_format_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_index(tmp, [], registry=None)
            with self.assertRaises(RegistryError) as ctx:
                registry.load_index(path)
            self.assertIn("'registry' format version", str(ctx.exception))

    def test_future_format_version_is_refused(self):
        """An old reader must reject what it cannot understand."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_index(tmp, [], registry=99)
            with self.assertRaises(RegistryError) as ctx:
                registry.load_index(path)
            self.assertIn("newer than the supported", str(ctx.exception))

    def test_missing_required_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_index(tmp, [{"name": "a", "path": "x.yaml"}])
            with self.assertRaises(RegistryError) as ctx:
                registry.load_index(path)
            self.assertIn("missing required key", str(ctx.exception))
            self.assertIn("version", str(ctx.exception))
            self.assertIn("sha256", str(ctx.exception))

    def test_duplicate_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp)
            row = entry(schema)
            path = write_index(tmp, [row, dict(row)])
            with self.assertRaises(RegistryError) as ctx:
                registry.load_index(path)
            self.assertIn("duplicate schema name", str(ctx.exception))

    def test_empty_schemas_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_index(tmp, [])
            with self.assertRaises(RegistryError) as ctx:
                registry.load_index(path)
            self.assertIn("non-empty list", str(ctx.exception))

    def test_not_a_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("- a\n- b\n")
            with self.assertRaises(RegistryError):
                registry.load_index(path)

    def test_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("registry: [unclosed\n")
            with self.assertRaises(RegistryError) as ctx:
                registry.load_index(path)
            self.assertIn("invalid YAML", str(ctx.exception))

    def test_missing_file(self):
        with self.assertRaises(RegistryError) as ctx:
            registry.load_index("/nope/registry.yaml")
        self.assertIn("cannot read index", str(ctx.exception))


class TestVerify(unittest.TestCase):
    def test_good_entry_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp)
            path = write_index(tmp, [entry(schema)])
            (report,) = registry.verify(registry.load_index(path))
            self.assertTrue(report["ok"], report["problems"])

    def test_wrong_checksum_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp)
            row = entry(schema, sha256="0" * 64)
            path = write_index(tmp, [row])
            (report,) = registry.verify(registry.load_index(path))
            self.assertFalse(report["ok"])
            self.assertIn("sha256 mismatch", report["problems"][0])

    def test_tampered_file_is_caught(self):
        """The point of the checksum: content changed under us."""
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp)
            path = write_index(tmp, [entry(schema)])
            with open(schema, "a", encoding="utf-8") as fh:
                fh.write("\n# injected\n")
            (report,) = registry.verify(registry.load_index(path))
            self.assertFalse(report["ok"])
            self.assertIn("sha256 mismatch", report["problems"][0])

    def test_missing_file_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp)
            path = write_index(tmp, [entry(schema)])
            os.unlink(os.path.join(tmp, "s.yaml"))
            (report,) = registry.verify(registry.load_index(path))
            self.assertFalse(report["ok"])
            self.assertIn("file not found", report["problems"][0])

    def test_structurally_invalid_schema_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp, "s.yaml", "format: t\nfields: []\n")
            path = write_index(tmp, [entry(schema)])
            (report,) = registry.verify(registry.load_index(path))
            self.assertFalse(report["ok"])
            self.assertIn("invalid schema", report["problems"][0])

    def test_no_schema_check_still_verifies_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp, "s.yaml", "format: t\nfields: []\n")
            path = write_index(tmp, [entry(schema)])
            (report,) = registry.verify(registry.load_index(path), check_schema=False)
            self.assertTrue(report["ok"], report["problems"])


class TestSearch(unittest.TestCase):
    def _entries(self, tmp):
        """Two real entries, loaded from an index, not hand-built dicts."""
        schema = write_schema(tmp)
        path = write_index(
            tmp,
            [
                entry(
                    schema,
                    name="jde_ar",
                    system="JD Edwards",
                    description="Accounts Receivable",
                    tags=["jde", "ar"],
                ),
                entry(
                    schema,
                    name="sap_batch",
                    system="SAP",
                    description="Batch header",
                    tags=["sap", "batch"],
                ),
            ],
        )
        return registry.load_index(path)

    def test_matches_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            hits = registry.search(self._entries(tmp), "jde")
            self.assertEqual([e.name for e in hits], ["jde_ar"])

    def test_matches_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            hits = registry.search(self._entries(tmp), "batch")
            self.assertEqual([e.name for e in hits], ["sap_batch"])

    def test_matches_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            hits = registry.search(self._entries(tmp), "receivable")
            self.assertEqual([e.name for e in hits], ["jde_ar"])

    def test_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                [e.name for e in registry.search(self._entries(tmp), "JD EDWARDS")],
                ["jde_ar"],
            )

    def test_empty_term_lists_all_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            hits = registry.search(self._entries(tmp), "")
            self.assertEqual([e.name for e in hits], ["jde_ar", "sap_batch"])

    def test_no_match_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(registry.search(self._entries(tmp), "zzz"), [])


class TestBuildIndex(unittest.TestCase):
    def test_computes_real_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_schema(tmp)  # not indexed, just present
            doc = registry.build_index([{"name": "s", "path": "s.yaml"}], tmp)
            self.assertEqual(doc["registry"], registry.REGISTRY_FORMAT_VERSION)
            self.assertEqual(
                doc["schemas"][0]["sha256"],
                registry.sha256_file(os.path.join(tmp, "s.yaml")),
            )

    def test_missing_file_raises(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(RegistryError),
        ):
            registry.build_index([{"name": "s", "path": "nope.yaml"}], tmp)

    def test_roundtrip_is_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_schema(tmp)
            doc = registry.build_index([{"name": "s", "path": "s.yaml"}], tmp)
            path = os.path.join(tmp, "registry.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(doc, fh)
            (report,) = registry.verify(registry.load_index(path))
            self.assertTrue(report["ok"], report["problems"])


class TestRegistryCli(unittest.TestCase):
    def _run(self, argv: list[str]) -> int:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return main(argv)

    def test_verify_ok(self):
        self.assertEqual(self._run(["registry", "verify", INDEX]), 0)

    def test_search_finds_something(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["registry", "search", INDEX, "cobol"])
        self.assertEqual(code, 0)
        self.assertIn("cobol_packed", out.getvalue())

    def test_search_no_match_exits_0(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["registry", "search", INDEX, "zzz"])
        self.assertEqual(code, 0)
        self.assertIn("no schema matches", out.getvalue())

    def test_bad_checksum_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = write_schema(tmp)
            path = write_index(tmp, [entry(schema, sha256="0" * 64)])
            self.assertEqual(self._run(["registry", "verify", path]), 3)

    def test_legacy_directory_form_still_works(self):
        """`registry <dir>` predates the subcommands and is in the README."""
        self.assertEqual(
            self._run(["registry", os.path.join(REPO_ROOT, "core", "formats")]), 0
        )

    def test_validate_subcommand(self):
        self.assertEqual(
            self._run(
                ["registry", "validate", os.path.join(REPO_ROOT, "core", "formats")]
            ),
            0,
        )

    def test_invalid_schema_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "broken.yaml"), "w", encoding="utf-8") as fh:
                fh.write("format: x\n")
            self.assertEqual(self._run(["registry", tmp]), 2)

    def test_empty_dir_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(["registry", tmp]), 1)

    def test_no_args_shows_usage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["registry"])
        self.assertEqual(code, 1)
        self.assertIn("usage:", err.getvalue())

    def test_verify_without_index(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["registry", "verify"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
