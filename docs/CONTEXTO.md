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
| `core/copybook.py`  | Copybook COBOL → schema YAML (FD/01/05 + PIC clauses)   |
| `core/crosscheck.py`| Reconciliación entre archivos (sum/count/keys)          |
| `core/diff.py`      | Diff por clave entre exports (added/removed/changed)    |

## Estado actual (2026-09-29, cierre de P2)

- **Rama `main`**, working tree limpio, sincronizada con `origin/main`.
- **345 tests OK** (1 skip — pyspark sin JVM), ruff y mypy limpios,
  CI verde en 3.10/3.11/3.12/3.13 (incluye el job `perf`).
- Las tres listas del tablero están cerradas salvo dos tarjetas que no son
  código (beca Anthropic, SaaS fuera de alcance). Detalle abajo.
- `v0.2.0` publicada en PyPI y GitHub. La 0.3.0 recogerá la ronda P2.

### Ronda P2 (2026-09-29) — cerrada

| Tarea | Commit | Nota |
|---|---|---|
| Normalización de texto por campo | `5ede7cb` | trim/case/NFKC, validados en `build_schema` |
| Máscara PII en output | `5ede7cb` | `full`/`partial`/`hash`, mismo bloque de código |
| Dedup por clave en streaming | `9aeb969` | `--key`, índice acotado con `--max-keys` |
| Excel con tipos nativos | `1b866f7` | `date`/`numeric` reales, `--excel-float-decimals` |
| Web UI: validación visual | `3335556` | `/validate`, raw resaltado, sin `innerHTML` |
| Registry público versionado | `fe545f9` | `docs/RFC_REGISTRY.md` + `registry.yaml` + search/verify |
| Conector Postgres | `cb247bf` | `--postgres-dsn`, `numeric` nunca `float` |
| Lint + tipos | `f64d9bd` | conexión fallida da mensaje, no traceback |
| `pyproject` extras | `33f05c3` | `all` incluye lint; extra `dev` y `postgres` |

### Fases

| Fase | Contenido | Estado |
|---|---|---|
| 0 | MVP fixed-width → JSON/CSV, YAML schema | ✅ |
| 1 | NDJSON/Parquet/Excel/SQL, autodetect, schemas built-in | ✅ |
| 2 | Parallel, batch glob, generate-schema, Pandas/Polars | ✅ |
| 3 | Rules, audit sidecars, registry, web UI | ✅ |
| 4 | Singer tap + Spark backend | ✅ parcial — falta SaaS y marketplace |
| 5 | Reconciliación, diff, COBOL, normalización, dedup, destinos | ✅ |

### Decisiones de diseño que conviene no olvidar

- **`--max-keys` en vez de HyperLogLog.** Una estimación no sirve cuando el
  error dice línea y valor. El límite falla en voz alta en vez de degradar.
- **NFKC no translitera entre alfabetos.** Normalizar compatibilidad no es
  adivinar el dato; hay test que lo fija.
- **`case`/`normalize` solo en campos string.** Aplicarlos a un decimal
  reordenaría en silencio lo que el schema dice.
- **Un valor enmascarado sale como string.** Si `2030...` siguiera siendo
  número, la auditoría estaría mintiendo sobre lo exportado.
- **`decimal` → `numeric` en Postgres, nunca `float`.** Un viaje de ida y
  vuelta por float mueve importes en silencio.
- **`rows_committed` ≠ `rows_written`.** El informe de carga cuenta solo lo
  que llegó; reportar lo encolado sería mentir sobre el estado de la tabla.
- **La web UI nunca usa `innerHTML`.** Un export con `<script>` en un campo
  se muestra como texto. Hay test que falla si alguien lo introduce.
- **Un checksum no autentica un índice.** El RFC lo dice: quien publica el
  índice puede reescribir el checksum. Firmar es gestión de claves, no formato.
- **El gate de performance es relativo y compara máquinas.** El baseline
  está medido en el Mac del usuario, así que en CI se salta a propósito.
- **Excel no tiene tipo solo-fecha:** las fechas se releen como `datetime`.

## Pendiente real

1. **Solicitud de beca Anthropic** — necesita datos del usuario
   (`https://claude.com/contact-sales/claude-for-oss`).
2. **Release v0.3.0** — recoger la ronda P2. Pendiente de decidir si BigQuery
   y S3 merecen conector propio: cambian la promesa (particionado, multipart)
   y no encajan en el diseño de batch secuencial del conector Postgres.
3. **Tarjeta de P2 marcada fuera de alcance** (SaaS / registry central / UI
   completa): se mantiene como registro de lo descartado, no como trabajo.

Detalle histórico de P0 y la primera ronda de P1 en las tablas siguientes.

### Plan P0/P1 (instructivo 2026-09-28) — cerrado

Histórico. El estado vigente está arriba.

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
| Plugin API v1 congelada + tests de contrato | ✅ | `b2e7af3` |
| Saneado de lint (`ruff`) que habría roto CI | ✅ | `ca179c5` |

### Ronda P1 alto valor (2026-09-29) — cerrada

| Tarea | Estado | Commit |
|---|---|---|
| COBOL COMP-3 (packed decimal): `type: packed` | ✅ | `243fd62` |
| COPYBOOK import → schema YAML | ✅ | `5f37738` |
| Validación cruzada entre archivos (`crosscheck`) | ✅ | `c58fcfa` |
| Modo diff entre exports (`diff`) | ✅ | `db9fd94` |
| Profiles de performance en CI | ✅ | `bee92f1` |
| Singer `STATE` real reanudable | ✅ | `15bbef1` |

Detalle de diseño que conviene no olvidar:

- **COMP-3** no pasa por decodificación de texto: es binario. `convert_field`
  lo enruta antes y el reporte lo muestra en hex.
- **PIC `V`**: `S9(7)V99` son 7 enteros + 2 decimales. Solo `(n)` repite; los
  `99` tras la `V` son dos posiciones, no noventa y nueve. Hay test.
- **`crosscheck` / `diff`** guardan un índice de claves, nunca el archivo. Sin
  techo: `--max-keys` falla en vez de crecer sin límite.
- **Gate de performance relativo** contra `perf-baseline.json`, nunca
  absoluto. La comprobación de determinismo sí es absoluta y no se relaja.
- A 100k líneas el parallel sigue siendo ~0.44x, es decir **más lento** que
  serial. El perfil lo deja medido; el parallel paga a escala GB.
- **Singer `--state`** solo aplica al formato singer (un test lo fija: antes
  un flag de resume truncaba una conversión normal por accidente).



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

## Pasos a seguir (tras el cierre de P1, 2026-09-29)

Las dos rondas P0/P1 del instructivo están cerradas (tablas arriba).

### 1. Publicar `v0.2.0` — en curso
- Bump de `version` en `pyproject.toml` a `0.2.0`.
- `git tag v0.2.0` + release notes en GitHub con el changelog de ambos lotes.
- **Republicar en PyPI requiere credenciales de twine del usuario**: queda
  fuera del alcance automatizable del agente.

### 2. Solicitud de OSS de Anthropic
- META activa: `https://claude.com/contact-sales/claude-for-oss`.
- Pendiente de reply del usuario con los datos de la solicitud.
- No bloquea el desarrollo: repo público, licencia MIT, descripción, topics
  y CI verde.

### 3. Candidatos de la cola P2 (con demanda real)
- Conectores destino (PG / BigQuery / S3).
- Normalización de texto por campo, máscara de PII, dedup en streaming.
- Excel con tipos nativos, validación visual en la web UI.

Fuera de alcance (§9 del instructivo): SaaS hosted, marketplace centralizado,
UI web completa. El registry público versionado solo con demanda.



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
