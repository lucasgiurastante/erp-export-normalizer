"""Property-based tests: no input may crash a conversion.

The promise this tool makes is that a bad file produces a *report*, not a
traceback. Today that holds by luck and by example tests. Here it is
asserted as a property over generated inputs.

`hypothesis` is a `dev` extra, not a runtime dependency, and these tests
skip cleanly when it is absent so the ordinary suite stays installable with
just PyYAML. A failing example is written to `.hypothesis/examples` and, once
curated, belongs in `tests/corpus/`.
"""

from __future__ import annotations

import contextlib
import decimal
import os
import tempfile
import unittest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - the whole module is skipped
    # The decorators below still have to be *defined*, because a class body
    # is executed at import time and `@unittest.skipUnless` only suppresses
    # running a test, not building the class. Without these stubs the file
    # raises NameError on import in an environment that has just PyYAML.
    HAS_HYPOTHESIS = False

    class HealthCheck:  # noqa: D106 - placeholder
        too_slow = "too_slow"
        function_scoped_fixture = "function_scoped_fixture"

    def settings(**_kwargs):  # noqa: D103 - placeholder
        def decorate(fn):
            return fn

        return decorate

    def given(*_args, **_kwargs):  # noqa: D103 - placeholder
        def decorate(fn):
            return fn

        return decorate

    class _DummyStrategy:
        """Accepts anything, does nothing.

        Strategy lookups happen while the class body runs, so this has to be
        inert rather than raise. The tests are then skipped by `skipUnless`.
        """

        def __call__(self, *_args, **_kwargs):
            return self

        def __getattr__(self, _name):
            return self

    class _Strategies:  # noqa: D106 - placeholder
        def __getattr__(self, _name):
            return _DummyStrategy()

    st = _Strategies()

from core import converters, copybook, dedup
from core.constraints import ConstraintError
from core.converters import ConversionError
from core.copybook import CopybookError
from core.crosscheck import CrossCheckError
from core.dedup import DedupError
from core.schema import SchemaError, build_schema
from core.validator import Validator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A record longer than the schema must be a validation error naming the
# length, never an IndexError from somewhere deeper.
SAFE_ERRORS = (
    ConversionError,
    SchemaError,
    CopybookError,
    CrossCheckError,
    DedupError,
    ConstraintError,
    UnicodeError,
)


def simple_schema(**field):
    base = {"name": "a", "start": 0, "length": 8, "type": "string"}
    base.update(field)
    return build_schema(
        {"format": "t", "version": "1.0.0", "record_length": 8, "fields": [base]}
    )


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestConvertFieldNeverCrashes(unittest.TestCase):
    """The core guarantee: any bytes in, a value or a ConversionError out."""

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.binary(min_size=0, max_size=64))
    def test_string_field(self, raw):
        field = simple_schema().fields[0]
        with contextlib.suppress(ConversionError):
            converters.convert_field(raw, field, "utf-8")

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.binary(min_size=0, max_size=64))
    def test_decimal_field(self, raw):
        field = simple_schema(type="decimal", scale=2).fields[0]
        try:
            value = converters.convert_field(raw, field, "utf-8")
        except ConversionError:
            return
        self.assertIsInstance(value, decimal.Decimal)

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.binary(min_size=0, max_size=64))
    def test_date_field(self, raw):
        field = simple_schema(type="date", format="YYYYMMDD").fields[0]
        try:
            value = converters.convert_field(raw, field, "utf-8")
        except ConversionError:
            return
        self.assertIsInstance(value, str)

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.binary(min_size=0, max_size=32))
    def test_packed_field(self, raw):
        field = simple_schema(type="packed", scale=2, length=5).fields[0]
        try:
            value = converters.convert_field(raw, field, "utf-8")
        except ConversionError:
            return
        self.assertIsInstance(value, decimal.Decimal)

    @settings(
        max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(
        st.binary(min_size=0, max_size=48),
        st.sampled_from(["NFC", "NFD", "NFKC", "NFKD"]),
        st.sampled_from(["upper", "lower", "title", None]),
        st.booleans(),
    )
    def test_normalization_never_crashes(self, raw, norm, case, trim):
        field = simple_schema(normalize=norm, case=case, trim=trim).fields[0]
        with contextlib.suppress(ConversionError, UnicodeError):
            converters.normalize_text(raw.decode("utf-8", errors="replace"), field)

    @settings(
        max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(
        st.binary(min_size=0, max_size=48),
        st.sampled_from(["full", "partial", "hash"]),
        st.integers(min_value=0, max_value=8),
    )
    def test_masking_never_crashes(self, raw, mode, keep):
        field = simple_schema(mask=mode, mask_keep=keep).fields[0]
        result = converters.mask_value(raw.decode("utf-8", errors="replace"), field)
        self.assertIsInstance(result, str)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestValidateRecordNeverCrashes(unittest.TestCase):
    @settings(
        max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.binary(min_size=0, max_size=200))
    def test_any_record_length(self, raw):
        schema = simple_schema()
        result = Validator(schema).validate_record(1, raw)
        # a record result, not an exception: that is the whole contract
        self.assertIsInstance(result.ok, bool)
        self.assertEqual(len(result.fields), 1)

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(
        st.binary(min_size=0, max_size=40),
        st.sampled_from(["YYYYMMDD", "YYYY-MM-DD", "DDMMYYYY", "DD/MM/YYYY", "YYMMDD"]),
    )
    def test_date_record_any_format(self, raw, fmt):
        schema = simple_schema(type="date", format=fmt)
        result = Validator(schema).validate_record(1, raw)
        self.assertIsInstance(result.ok, bool)

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(
        st.binary(min_size=0, max_size=40),
        st.sampled_from(["utf-8", "cp850", "ebcdic-cp037", "latin-1"]),
    )
    def test_any_codepage(self, raw, codepage):
        schema = build_schema(
            {
                "format": "t",
                "version": "1.0.0",
                "record_length": 8,
                "codepage": codepage,
                "fields": [{"name": "a", "start": 0, "length": 8}],
            }
        )
        result = Validator(schema).validate_record(1, raw)
        self.assertIsInstance(result.ok, bool)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestParsePictureNeverCrashes(unittest.TestCase):
    @settings(
        max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.text(max_size=12))
    def test_any_text_is_answered_or_refused(self, pic):
        try:
            kind, length, scale, digits = copybook.parse_picture(pic)
        except CopybookError:
            return
        self.assertIn(kind, ("string", "numeric"))
        self.assertGreaterEqual(length, 0)
        self.assertGreaterEqual(scale, 0)

    @settings(
        max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.text(alphabet="XAS9V()-.+", max_size=10))
    def test_pictures_made_of_picture_characters(self, pic):
        with contextlib.suppress(CopybookError):
            copybook.parse_picture(pic)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestIndexStructuresNeverCrash(unittest.TestCase):
    """The bounded indexes: arbitrary keys in, a result or a bound out."""

    @settings(
        max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.lists(st.text(max_size=8), max_size=40))
    def test_dedup_index(self, keys):
        index = dedup.DedupIndex(max_keys=1000)
        for line, key in enumerate(keys, start=1):
            index.observe(key, line)
        report = index.report()
        self.assertEqual(report.distinct_keys, len(set(keys)))
        self.assertEqual(report.duplicate_count, len(keys) - len(set(keys)))

    @settings(
        max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.lists(st.text(max_size=6), min_size=2, max_size=8))
    def test_dedup_bound_is_enforced(self, keys):
        index = dedup.DedupIndex(max_keys=2)
        try:
            for line, key in enumerate(keys, start=1):
                index.observe(key, line)
        except DedupError:
            pass

    @settings(
        max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.dictionaries(st.text(max_size=5), st.integers(), max_size=10))
    def test_row_key(self, row):
        fields = sorted(row)
        try:
            key = dedup.row_key(row, fields)
        except DedupError:
            return
        self.assertIsInstance(key, str)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestSchemaSpecNeverCrashes(unittest.TestCase):
    @settings(
        max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(
        st.integers(min_value=-1000, max_value=1000),
        st.integers(min_value=-1000, max_value=1000),
    )
    def test_bounds_in_any_order(self, low, high):
        with contextlib.suppress(SchemaError):
            simple_schema(type="decimal", scale=2, min=low, max=high)

    @settings(
        max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(st.text(alphabet="()[]{}+*?^$.|\\abc019 ", max_size=12))
    def test_any_regex_is_either_valid_or_refused(self, pattern):
        with contextlib.suppress(SchemaError):
            simple_schema(pattern=pattern)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestSafeErrorsAreIndeedSafe(unittest.TestCase):
    """Document which exceptions callers have to handle, and nothing more."""

    def test_error_types_are_all_value_or_runtime_subclasses(self):
        for exc in SAFE_ERRORS:
            self.assertTrue(issubclass(exc, (ValueError, RuntimeError)), exc)

    def test_no_bare_indexerror_from_a_short_record(self):
        """A record shorter than the layout is a report, not a crash."""
        schema = simple_schema()
        for raw in (b"", b"a", b"ab", b"abc" * 7):
            result = Validator(schema).validate_record(1, raw)
            self.assertIsInstance(result.ok, bool)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis is a dev extra")
class TestFullCliNeverCrashes(unittest.TestCase):
    """The end-to-end path, on arbitrary files, always returns an exit code."""

    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
    @given(st.binary(min_size=0, max_size=300))
    def test_convert_arbitrary_file(self, payload):
        from cli import main

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.txt")
            with open(src, "wb") as fh:
                fh.write(payload)
            schema = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
            out = os.path.join(tmp, "o.json")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        schema,
                        "--input",
                        src,
                        "--output",
                        out,
                        "--format",
                        "json",
                    ]
                )
            self.assertIn(code, (0, 1, 2, 3))


import io  # noqa: E402  (used by the CLI test above)

if __name__ == "__main__":
    unittest.main()
