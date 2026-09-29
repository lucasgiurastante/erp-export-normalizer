"""Versioned schema registry: load, search and verify (local, no network).

Implements the local half of `docs/RFC_REGISTRY.md`. Everything is a file
on disk: an index is a YAML document listing schemas with their version and
a SHA-256, so a downloaded schema can be checked without trusting the
source that delivered it.

What a checksum does **not** do is protect against someone who can rewrite
the index: they can rewrite the checksum too. Authenticating the index needs
signing, which is a key-management problem and deliberately out of scope
here. The RFC says so out loud so nobody mistakes this for a trust store.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os

import yaml

from .schema import SchemaError, build_schema

REGISTRY_FORMAT_VERSION = 1

REQUIRED_ENTRY_KEYS = ("name", "version", "path", "sha256")


class RegistryError(ValueError):
    """The index is malformed, or a referenced schema fails verification."""


@dataclasses.dataclass(frozen=True)
class Entry:
    """One schema listed in an index."""

    name: str
    version: str
    path: str
    sha256: str
    system: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    index_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "path": self.path,
            "sha256": self.sha256,
            "system": self.system,
            "description": self.description,
            "tags": list(self.tags),
            "index_path": self.index_path,
        }

    def haystack(self) -> str:
        return " ".join(
            [self.name, self.system, self.description, " ".join(self.tags)]
        ).lower()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_index(path: str) -> list[Entry]:
    """Read an index file and return its entries.

    The index format version is checked first: an old reader must reject an
    index it cannot understand rather than misread it.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise RegistryError(f"cannot read index {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise RegistryError(f"{path}: index must be a mapping")
    fmt = data.get("registry")
    if not isinstance(fmt, int):
        raise RegistryError(f"{path}: missing integer 'registry' format version")
    if fmt > REGISTRY_FORMAT_VERSION:
        raise RegistryError(
            f"{path}: index format v{fmt} is newer than the supported "
            f"v{REGISTRY_FORMAT_VERSION}; upgrade erp-export-normalizer"
        )
    raw_entries = data.get("schemas")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RegistryError(f"{path}: 'schemas' must be a non-empty list")

    base = os.path.dirname(os.path.abspath(path))
    entries: list[Entry] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise RegistryError(f"{path}: schemas[{i}] must be a mapping")
        missing = [k for k in REQUIRED_ENTRY_KEYS if not raw.get(k)]
        if missing:
            raise RegistryError(
                f"{path}: schemas[{i}] missing required key(s): " + ", ".join(missing)
            )
        name = str(raw["name"])
        if name in seen:
            raise RegistryError(f"{path}: duplicate schema name {name!r}")
        seen.add(name)
        entries.append(
            Entry(
                name=name,
                version=str(raw["version"]),
                path=str(raw["path"]),
                sha256=str(raw["sha256"]).lower(),
                system=str(raw.get("system", "")),
                description=str(raw.get("description", "")),
                tags=tuple(str(t) for t in (raw.get("tags") or ())),
                index_path=base,
            )
        )
    return entries


def resolve(entry: Entry) -> str:
    """Absolute path of the schema a registry entry points at."""
    if os.path.isabs(entry.path):
        return entry.path
    return os.path.join(entry.index_path, entry.path)


def search(entries: list[Entry], term: str = "") -> list[Entry]:
    """Substring search over name, system, description and tags.

    An empty term lists everything, which is what a caller browsing the
    catalogue wants. Order is by name so the output is reproducible.
    """
    needle = term.strip().lower()
    if not needle:
        return sorted(entries, key=lambda e: e.name)
    return sorted((e for e in entries if needle in e.haystack()), key=lambda e: e.name)


def verify(entries: list[Entry], check_schema: bool = True) -> list[dict]:
    """Check every entry: file present, checksum matches, schema loads.

    `check_schema` also runs `build_schema`, so an entry that points at a
    structurally invalid schema fails here rather than mid-conversion.
    """
    report: list[dict] = []
    for entry in entries:
        problems: list[str] = []
        path = resolve(entry)
        if not os.path.exists(path):
            problems.append(f"file not found: {path}")
        else:
            actual = sha256_file(path)
            if actual != entry.sha256:
                problems.append(
                    f"sha256 mismatch: manifest {entry.sha256}, file {actual}"
                )
            elif check_schema:
                try:
                    with open(path, encoding="utf-8") as fh:
                        build_schema(yaml.safe_load(fh))
                except (OSError, yaml.YAMLError) as exc:
                    problems.append(f"cannot read schema: {exc}")
                except SchemaError as exc:
                    problems.append(f"invalid schema: {exc}")
        report.append(
            {
                "name": entry.name,
                "version": entry.version,
                "ok": not problems,
                "problems": problems,
            }
        )
    return report


def build_index(entries_spec: list[dict], root: str, updated: str = "") -> dict:
    """Build an index document from `{name, path, ...}` descriptions.

    Checksums are computed from the real files, so the generated index is
    verifiable by construction. This is what a publisher runs.
    """
    schemas: list[dict] = []
    for spec in entries_spec:
        rel = spec["path"]
        full = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.exists(full):
            raise RegistryError(f"{spec['name']}: file not found: {full}")
        row = {
            "name": spec["name"],
            "path": rel,
            "version": str(spec.get("version", "1.0.0")),
            "sha256": sha256_file(full),
        }
        for key in ("system", "description", "tags"):
            if spec.get(key):
                row[key] = spec[key]
        schemas.append(row)
    return {
        "registry": REGISTRY_FORMAT_VERSION,
        "updated": updated,
        "schemas": schemas,
    }
