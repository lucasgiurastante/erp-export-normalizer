# Plugin API v1 (frozen)

Status: **frozen**. Version: `1.0`.

Scope: custom record readers (`Reader`) loaded via `core/plugins.py`
(`discover` / `load_reader` / `PluginError`). Schema selects the plugin
with the `parser:` field (see `examples/framed_schema.yaml` and
`core/plugin_examples/length_prefixed_frame.py`).

Reference implementation of the contract: `parser.FixedWidthReader`
(`core/parser.py`) and the bundled example
`core/plugin_examples/length_prefixed_frame.py`.

## 1. Discovery

- A plugin is one Python module `<name>.py` inside a plugins directory.
- Default dir: `core/plugin_examples/` (`DEFAULT_PLUGINS_DIR`).
  Custom dir via `--plugins-dir` (passed as `plugins_dir` to
  `discover` / `load_reader`).
- `discover(plugins_dir)` lists modules sorted by filename, skipping
  files starting with `_` and non-`.py` files.
- The schema field `parser: <name>` must match the module name
  (without `.py`). Unknown name → `PluginError` (`"plugin '<name>'
  not found in ..."`).
- Module must define a top-level `Reader` class. Missing `Reader` →
  `PluginError` (`"plugin '<name>': missing 'Reader' class"`).

## 2. Class: `Reader`

```python
from collections.abc import Iterator


class Reader:
    def __init__(self, schema, path: str): ...
    def records(self, skip_first: bool = False) -> Iterator[tuple[int, bytes]]: ...
```

### `__init__(self, schema, path)`

- Positional signature `(schema, path)` is **mandatory**.
- `schema` is the parsed `Schema` object (`core/schema.py`); `path` is
  the input file path (`str`).
- Must accept both arguments positionally:
  `reader_cls(schema, path)`. Extra optional parameters are allowed
  only with defaults (see §5).
- Must not require reading the file at construction time; open the
  file inside `records()`. Store `schema`/`path` as attributes
  (convention: `self.schema`, `self.path`).

### `records(self, skip_first=False)`

- Returns an iterator yielding `(lineno, record_bytes)` tuples.
- `skip_first: bool = False` is **mandatory** (positional-or-keyword,
  default `False`). When `True`, the first record (`lineno == 1`) is
  skipped — e.g. a header frame/row.
- Yield type is exactly `tuple[int, bytes]`: 1-based line/frame number
  and raw record bytes (see §3).
- Must be lazy/streaming: open the file in binary mode inside the
  generator (`with open(self.path, "rb")`) and `yield` one record at a
  time. Must not load the whole file into memory.
- Order: records yielded in file order with strictly increasing
  `lineno` starting at `1`.
- Empty payloads (`b""`): may be yielded; `plugins.iter_records()`
  skips them downstream. Plugins should not rely on empty records
  surviving the pipeline.

## 3. Raw-bytes semantics

- `record_bytes` are **raw, undecoded bytes** — no `decode()` inside
  the plugin. Decoding/validation happens downstream per schema
  (`codepage`, field types).
- Framing/stripping is the plugin's decision. Example:
  `length_prefixed_frame` reads a 2-byte big-endian length header,
  strips it, and yields only the payload, so schema `start`/`length`
  offsets are relative to the payload, not the wire record.
- Schema `start`/`length` are **advisory** for plugins; the plugin
  decides how to slice. `record_length` is **not required** when
  `parser:` is set (validation still runs afterwards with the same
  cumulative error report and determinism guarantees).

## 4. Errors

- Malformed content (truncated header/frame, bad length, corrupt
  record) → `ValueError` with a message containing location info
  (offset or frame number). Example:
  `"truncated frame: expected 16 bytes, got 5"`.
- I/O problems (file not found, unreadable) → `OSError` (propagated
  from `open()`; do not wrap).
- Load problems (plugin not found, module cannot load, missing
  `Reader` class, incompatible `__init__` signature) → `PluginError`
  (a `ValueError` subclass defined in `core/plugins.py`). Messages must
  name the plugin: `"plugin '<name>': ..."`. A `TypeError` raised by an
  incompatible constructor is a contract violation and loaders
  MUST surface it as `PluginError` (wrapping the original `TypeError`
  as cause).
- Plugins must not swallow exceptions or `print()`; just raise.

## 5. Versioning and compatibility

- This document is API **v1** (`1.0`), versioned with semver.
- Rule: any `v1.x` keeps the constructor and method signatures
  compatible:
  - `Reader(schema, path)` keeps working positionally;
  - `records(skip_first=False)` keeps yielding `(int, bytes)` in order.
- Allowed in `v1.x`: new optional parameters (with defaults), new
  optional methods/attributes, clarifications, new bundled example
  plugins, broader input tolerance.
- Forbidden in `v1.x`: renaming/removing `Reader`, `__init__`
  parameters or `records`/`skip_first`; changing the yield shape
  (order, types, 1-based numbering); turning `ValueError`/`OSError`
  cases into silent behavior. Any such break requires a **v2** document
  and a new major contract.

## 6. Minimal example

```python
"""Minimal v1 plugin: line-oriented raw reader."""

from collections.abc import Iterator


class Reader:
    def __init__(self, schema, path: str):
        self.schema = schema
        self.path = path

    def records(self, skip_first: bool = False) -> Iterator[tuple[int, bytes]]:
        with open(self.path, "rb") as fh:
            lineno = 0
            for raw in fh:
                record = raw.rstrip(b"\r\n")
                if not record:
                    continue
                lineno += 1
                if skip_first and lineno == 1:
                    continue
                yield lineno, record
```

Schema wiring:

```yaml
format: framed
version: 1.0.0
parser: my_minimal_reader
codepage: cp1252
fields:
  - {name: id, start: 0, length: 4}
```
