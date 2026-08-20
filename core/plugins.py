"""Custom record parsers (plugins) for formats the built-in readers cannot
handle: binary records, framed payloads, packed decimals, etc.

A plugin is a Python module in `core/plugin_examples/` (bundled examples) or a
user directory passed via `--plugins-dir`, exposing a `Reader` class with a
`records()` method yielding `(line_no, record_bytes)` — the same contract as
`parser.FixedWidthReader`. The schema selects it via the `parser` field::

    format: framed
    parser: length_prefixed_frame
    fields:
      - {name: id,   length: 4}
      - {name: date, length: 8, type: date, format: YYYYMMDD}

Field `start`/`length` are advisory for plugins: the plugin decides how to
slice. `record_length` is not required. Validation still runs afterwards, so
plugins get the same cumulative error report and determinism guarantees.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from .schema import Schema

DEFAULT_PLUGINS_DIR = os.path.join(os.path.dirname(__file__), "plugin_examples")


class PluginError(ValueError):
    """A plugin could not be loaded or is not a valid Reader."""


@dataclass(frozen=True)
class Plugin:
    name: str
    module_path: str


def discover(plugins_dir: str = DEFAULT_PLUGINS_DIR) -> list[Plugin]:
    """List available plugins (modules under `plugins_dir`)."""
    if not os.path.isdir(plugins_dir):
        return []
    out: list[Plugin] = []
    for entry in sorted(os.listdir(plugins_dir)):
        if not entry.endswith(".py") or entry.startswith("_"):
            continue
        out.append(
            Plugin(name=entry[:-3], module_path=os.path.join(plugins_dir, entry))
        )
    return out


def load_reader(
    name: str, schema: Schema, path: str, plugins_dir: str = DEFAULT_PLUGINS_DIR
):
    """Instantiate the `Reader` class from plugin `name`."""
    for plugin in discover(plugins_dir):
        if plugin.name == name:
            return _instantiate(plugin, schema, path)
    raise PluginError(f"plugin '{name}' not found in {plugins_dir}")


def _instantiate(plugin: Plugin, schema: Schema, path: str):
    spec = importlib.util.spec_from_file_location(
        f"erp_plugin_{plugin.name}", plugin.module_path
    )
    if spec is None or spec.loader is None:
        raise PluginError(f"plugin '{plugin.name}': cannot load module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    reader_cls = getattr(module, "Reader", None)
    if reader_cls is None:
        raise PluginError(f"plugin '{plugin.name}': missing 'Reader' class")
    return reader_cls(schema, path)


def iter_records(reader) -> Iterator[tuple[int, bytes]]:
    """Yield (line_no, record_bytes) from a plugin Reader, skipping empty lines."""
    for lineno, record in reader.records():
        if isinstance(record, bytes) and not record:
            continue
        yield lineno, record
