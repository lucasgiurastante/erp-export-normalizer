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

## Estado actual (2026-09-29)

- **Rama `main`**, working tree limpio, sincronizado con GitHub
  (lucasgiurastante/erp-export-normalizer).
- **113 tests OK** (1 skip — pyspark sin JVM), ruff limpio, mypy limpio.
- P0-1..P0-5 + P1-1 + P1-2 + P1-3 completados (ver tabla abajo).
- Añadido **Plugin API v1 congelada** (`docs/PLUGIN_API_v1.md`) con 4 tests de
  contrato (`tests/test_plugin_api.py`).
- Lint repo saneado: `ruff check` + `ruff format` limpios en los 9 ficheros
  que los commits anteriores dejaron sin formatear (`core/rules.py`,
  `core/schema.py`, `core/validator.py`, `scripts/bench_parallel.py`, tests).
  La regla `SIM102` de `core/schema.py` se resolvió fusionando la validación
  de `type: date` en un solo `if`/`elif`.

### Fases

| Fase | Contenido | Estado |
|------|-----------|--------|
| 0 | MVP fixed-width → JSON/CSV, YAML schema | ✅ |
| 1 | NDJSON/Parquet/Excel/SQL, autodetect, schemas built-in | ✅ |
| 2 | Parallel, batch glob, generate-schema, Pandas/Polars | ✅ |
| 3 | Rules, audit sidecars, registry, web UI | ✅ |
| 4 | Singer tap + Spark backend | ✅ parcial — falta SaaS y marketplace |

### Plan P0/P1 (instructivo 2026-09-28) — cerrado

| Tarea | Estado | Commit |
|---|---|---|
| P0-1 `JsonWriter` streaming (memoria O(1)) | ✅ | `a29334e` |
| P0-2 4 formatos de fecha extra (`YYYY-MM-DD`, `DDMMYYYY`, `DD/MM/YYYY`, `YYMMDD`) | ✅ | `cc8ba37` |
| P0-3 detector: muestra 20, scoring parcial, `None` en ambigüedad | ✅ | `3b8056f` |
| P0-4 rules: no-numéricos como violación + validación de spec en `__init__` | ✅ | `1a12c66` |
| P0-5 flag `--chunk-lines` + `scripts/bench_parallel.py` | ✅ | `76a9bf7` |
| P1-1 test Spark sin JVM (mock `SparkSession`) | ✅ | `3205589` |
| P1-2 schemas `sap_fi_bseg` + `jde_gl_distinct` | ✅ | `6f559e5` |
| P1-3 errores con `field` + `raw` truncado a 50 chars | ✅ | `f866fb1` |
| Plugin API v1 congelada + tests de contrato | ✅ | este commit |


### Gaps detectados (análisis 2026-08-20)

1. ~~**.DS_Store commiteado**~~ ✅ resuelto — fuera del índice, `.gitignore` cubre.
2. ~~**Sin tags/releases**~~ ✅ resuelto — tag + release `v0.1.0` creados con release notes.
3. ~~**About de GitHub vacío**~~ ✅ resuelto — description + 8 topics (erp, jdedwards, sap, etl, flat-file, mainframe, fixed-width, python).
4. ~~**Sin CI badge**~~ ✅ resuelto — badge del workflow en README (activo tras push post-fix).
5. ~~**Biblioteca formats/ pobre**~~ ✅ resuelto — 8 schemas (jde_ar/ap/gl,
   jde_gl_distinct, sap_batch, sap_fi_document, sap_fi_bseg, cobol_fixed) +
   tests de detector por fixture.
6. ~~**plugins/**~~ ✅ resuelto — sistema de plugins operativo (`core/plugins.py`):
   `discover`/`load_reader`, schema opcional `parser:`, `--plugins-dir`, con el
   plugin de ejemplo `core/plugin_examples/length_prefixed_frame.py` (frames
   binarios), contrato congelado en `docs/PLUGIN_API_v1.md` y 10 tests
   (`tests/test_plugins.py` + `tests/test_plugin_api.py`).
7. ~~**io.py spark backend sin test local**~~ ✅ resuelto — `tests/test_phase4.py`
   mockea `SparkSession` (`createDataFrame().collect()`) y testea el mapeo de
   tipos sin JVM. El skip restante del suite es otro (pyspark no instalado).
8. ~~**Sin presencia en PyPI**~~ ✅ resuelto — `erp-export-normalizer` 0.1.0 publicado
   (https://pypi.org/project/erp-export-normalizer/). Wheel autocontenida: schemas
   y plugin ejemplo dentro del paquete (`core/formats/`, `core/plugin_examples/`).
   Verificado: `pip install erp-export-normalizer` + auto-detección/COBOL/plugin OK.
   `examples/` con fixtures JDE/SAP/COBOL/binario + comandos copy-paste.
9. ~~**Lint roto en los commits P0/P1**~~ ✅ resuelto (2026-09-29) — 9 ficheros
   sin `ruff format` ni `E501`/`SIM102`/`B905` limpios. CI habría fallado en
   `lint`. Ahora `ruff check` + `ruff format --check` verdes.


### Fix lateral detectado y resuelto

- **Bug de codepage EBCDIC** — el schema aceptaba `ebcdic-cp037` pero Python usa
  el codec `cp037`. Agregado `converters.codec_for()` como alias aplicado en
  validator/generator; el schema `cobol_fixed.yaml` lo usa y es el primer caso
  EBCDIC real de la biblioteca.

## Pasos a seguir (siguiente ronda, 2026-09-29)

El plan P0/P1 del instructivo 2026-09-28 está cerrado (tabla arriba).
Pendientes reales, por valor:

### 1. Publicar `v0.2.0`
- Tag + release notes con: `JsonWriter` streaming, 4 formatos de fecha,
  detector con desempate ambiguo, rules sin silencios, `--chunk-lines`,
  `sap_fi_bseg` + `jde_gl_distinct`, Plugin API v1.
- Bump de `version` en `pyproject.toml` + republicar wheel en PyPI.

### 2. Solicitud de OSS de Anthropic
- META activa: `https://claude.com/contact-sales/claude-for-oss`.
- Pendiente de reply del usuario con los datos de la solicitud.

### 3. COBOL COMP-3
- `core/formats/` tiene `cobol_fixed` EBCDIC pero no packed decimal.
- Requiere un reader binario → candidato natural a plugin con el contrato v1
  ya congelado. Solo con demanda real de cliente.

### 4. Fases futuras
- Batch de schemas por cliente (recomendación: 1 repo por cliente, schema
  compartido = parseo idéntico).
- Sin SaaS hosted ni marketplace (fuera de alcance, ver §9 del instructivo).


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
