import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import registry  # noqa: E402

META = {
    "jde_ar": (
        "JD Edwards",
        "Accounts Receivable export",
        ["jde", "ar", "fixed-width"],
    ),
    "jde_ap": ("JD Edwards", "Accounts Payable export", ["jde", "ap", "fixed-width"]),
    "jde_gl": ("JD Edwards", "General Ledger export", ["jde", "gl", "fixed-width"]),
    "jde_gl_distinct": (
        "JD Edwards",
        "General Ledger export with DISTINCT set",
        ["jde", "gl", "distinct"],
    ),
    "jde_ar_crdb": (
        "JD Edwards",
        "General Ledger with CR/DB credit-debit signs",
        ["jde", "gl", "crdb", "credit-debit"],
    ),
    "sap_batch": ("SAP", "Batch export (fixed width)", ["sap", "batch", "fixed-width"]),
    "sap_fi_document": (
        "SAP",
        "Accounting document headers, tab delimited as SAP exports it",
        ["sap", "fi", "bkpf", "tab-delimited"],
    ),
    "sap_fi_bseg": (
        "SAP",
        "Accounting document line items (BSEG), tab delimited, sign in SHKZG",
        ["sap", "fi", "bseg", "tab-delimited"],
    ),
    "cobol_fixed": (
        "COBOL",
        "EBCDIC CP037 sequential export",
        ["cobol", "ebcdic", "mainframe"],
    ),
    "cobol_packed": (
        "COBOL",
        "EBCDIC CP037 with COMP-3 packed decimal amounts",
        ["cobol", "ebcdic", "comp-3", "packed"],
    ),
}

specs = []
for name, (system, desc, tags) in META.items():
    path = f"core/formats/{name}.yaml"
    with open(path, encoding="utf-8") as fh:
        version = str(yaml.safe_load(fh).get("version", "1.0.0"))
    specs.append(
        {
            "name": name,
            "path": path,
            "version": version,
            "system": system,
            "description": desc,
            "tags": tags,
        }
    )

doc = registry.build_index(specs, ".", updated="2026-09-30")
with open("registry.yaml", "w", encoding="utf-8") as fh:
    yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
print("registry.yaml regenerado con", len(doc["schemas"]), "schemas")
