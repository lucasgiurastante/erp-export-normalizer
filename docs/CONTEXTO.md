# Contexto del proyecto: erp-export-normalizer

> Documento de trabajo interno — contexto, estado actual y pasos a seguir.
> Para qué es, qué hay hecho, qué falta y en qué orden hacerlo.

## Qué es

Convertidor CLI, schema-driven y streaming, para archivos planos legacy de ERP
(fixed-width de JD Edwards, SAP, dumps de mainframe) → JSON/CSV/NDJSON/SQL/
Parquet/Excel/Singer. Validado, con reporte de errores por línea, sin cargar
todo el archivo en memoria.

Principios de diseño (no negociables):

- **Streaming** — archivos multi-GB con memoria constante.
- **Determinista** — mismo input + mismo schema = mismo output. Base del audit.
- **Sin red, sin base de datos** — corre en entornos air-gapped.
- **Schema-first** — el YAML describe el archivo; el parser es genérico. El
  conocimiento es portable: un schema compartido = parseo idéntico en cualquier
  empresa.

## Stack

- Python 3.10+ (probado con 3.14), stdlib unittest, pytest compatible.
- Deps core: solo PyYAML. Opcionales: pyarrow, openpyxl, pandas, polars, pyspark.
- Lint/typecheck: ruff + mypy (mirror de CI en `.github/workflows`).

## Arquitectura

```
input.txt ──► [detector] ──► [parser (schema YAML)] ──► [converters] ──► [validator] ──► [writer]
                  │                    │                       │                    │
                  │                    └── formats/            └── dates/decimal/  ├── JSON/CSV/NDJSON/SQL/Singer
                  └── auto-detect                              codepages/EBCDIC    ├── Parquet/Excel
                                                                                    └── report + audit
```

| Módulo              | Responsabilidad                                        |
|---------------------|--------------------------------------------------------|
| `cli.py`            | Entry point, argparse, exit codes 0/1/2/3              |
| `core/schema.py`    | Cargar/validar YAML (start, length, type, codepage)    |
| `core/parser.py`    | Slicing fixed-width + delimited, streaming línea a línea |
| `core/detector.py`  | Autodetección heurística contra formats/               |
| `core/converters.py`| Fechas, decimales (scale), codepages (CP850/EBCDIC)    |
| `core/validator.py` | Errores acumulativos con número de línea               |
| `core/writer.py`    | JSON/CSV/NDJSON/SQL + Parquet/Excel (opcional)         |
| `core/generator.py` | Inferencia de schema desde archivo delimitado          |
| `core/parallel.py`  | Multiprocesing chunked, output determinista            |
| `core/io.py`        | `read_erp()` → Pandas/Polars/Spark DataFrames          |
| `core/rules.py`     | Reglas de negocio: sum / balance, O(1) memoria         |
| `core/audit.py`     | SHA-256 + resumen de conversión (sidecar)              |
| `core/webui.py`     | Web UI zero-dependency (schema generation + preview)   |

## Estado actual (agosto 2026)

- **8 commits**, HEAD `ed1143c` ("feat: expand built-in schema library to 6 formats + CI badge").
- **66 tests OK** (1 skip — spark sin JVM local), ruff limpio, mypy limpio.
- Tag + release `v0.1.0`; CI verde en GitHub (incluye los últimos pushes).
- Git sync con GitHub (lucasgiurastante/erp-export-normalizer), working tree limpio.

### Fases

| Fase | Contenido | Estado |
|------|-----------|--------|
| 0 | MVP fixed-width → JSON/CSV, YAML schema | ✅ |
| 1 | NDJSON/Parquet/Excel/SQL, autodetect, schemas built-in | ✅ |
| 2 | Parallel, batch glob, generate-schema, Pandas/Polars | ✅ |
| 3 | Rules, audit sidecars, registry, web UI | ✅ |
| 4 | Singer tap + Spark backend | ✅ parcial — falta SaaS y marketplace |

### Gaps detectados (análisis 2026-08-20)

1. ~~**.DS_Store commiteado**~~ ✅ resuelto — fuera del índice, `.gitignore` cubre.
2. ~~**Sin tags/releases**~~ ✅ resuelto — tag + release `v0.1.0` creados con release notes.
3. ~~**About de GitHub vacío**~~ ✅ resuelto — description + 8 topics (erp, jdedwards, sap, etl, flat-file, mainframe, fixed-width, python).
4. ~~**Sin CI badge**~~ ✅ resuelto — badge del workflow en README (activo tras push post-fix).
5. ~~**Biblioteca formats/ pobre**~~ ✅ resuelto — 6 schemas (jde_ar/ap/gl, sap_batch/fi_document, cobol_fixed) + tests de detector por fixture.
6. ~~**plugins/**~~ ✅ resuelto — sistema de plugins operativo (`core/plugins.py`):
   `discover`/`load_reader`, schema opcional `parser:`, `--plugins-dir`, con el
   plugin de ejemplo `plugins/length_prefixed_frame.py` (frames binarios) y 6 tests.
7. **io.py spark backend sin test local** — el skip del suite es ese (requiere JVM).

### Fix lateral detectado y resuelto

- **Bug de codepage EBCDIC** — el schema aceptaba `ebcdic-cp037` pero Python usa
  el codec `cp037`. Agregado `converters.codec_for()` como alias aplicado en
  validator/generator; el schema `cobol_fixed.yaml` lo usa y es el primer caso
  EBCDIC real de la biblioteca.

## Pasos a seguir (orden propuesto)

### 1. Higiene repo (rápido, urgente)
```bash
git rm --cached .DS_Store
# añadir ".DS_Store" a .gitignore
git add .gitignore && git commit -m "chore: remove .DS_Store, ignore macOS junk"
git push
```

### 2. Visibilidad GitHub
- Añadir description: "Schema-driven streaming converter for legacy ERP flat files (JD Edwards, SAP) to JSON/CSV/Parquet — air-gapped, deterministic, auditable."
- Topics: `erp`, `jdedwards`, `sap`, `etl`, `flat-file`, `mainframe`, `fixed-width`, `python`.
- Tag `v0.1.0` + release notes en GitHub.

### 3. CI badge en README
- Badge de estado del workflow (GitHub Actions) tras el primer push post-fix.

### 4. Biblioteca formats/
- Añadir 3-4 schemas reales con fixtures de test: SAP FI (BKPF/BSEG), JDE AP, JDE GL, COBOL COPYBOOK simple.
- Cada schema = fixture + test de detector. Esto multiplica el valor del autodetect.

### 5. Tests spark backend
- Quitar el skip en `tests/test_phase4.py` (mock de SparkSession si no hay JVM local).

### 6. plugins/ hooks (fase futura)
- API de parser custom para formatos binarios raros. Solo cuando haya demanda real.

### 7. Fuera de alcance CLI
- SaaS hosted, marketplace de schemas, registry comunitario centralizado.

## Loop de desarrollo

```bash
.venv/bin/python -m unittest discover -s tests -v   # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy cli.py core
.venv/bin/erp-normalize --help                       # smoke test CLI
```

## Recordatorios

- Commit style: conventional (`feat:`, `chore:`, `fix:`, `docs:`).
- Regla: un commit por paso lógico; push tras cada paso completo.
- No romper: determinismo + streaming son la promesa del producto.
