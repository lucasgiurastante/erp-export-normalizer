# INSTRUCTIVO PARA AGENTE IA — erp-export-normalizer (continuación)

> Lee esto primero. Te deja operativo en 10 min. No reinventes. Sigue el orden P0 → P1 → P2. Un commit por paso lógico. No rompas determinismo + streaming.

## 1. Qué es

CLI schema-driven, streaming, para planos legacy ERP (JDE, SAP, mainframe fixed-width) → JSON/CSV/NDJSON/SQL/Parquet/Excel/Singer. Validado, reporte por línea, air-gapped, determinista.

Principios no negociables:
- **Streaming** — multi-GB con memoria constante.
- **Determinista** — mismo input + mismo schema = mismo output.
- **Sin red, sin DB** — corre air-gapped.
- **Schema-first** — el YAML describe el archivo; el parser es genérico.

## 2. Setup (copy-paste)

```bash
cd "/Users/sabbat/Library/CloudStorage/GoogleDrive-yurastantelucas@gmail.com/Mi unidad/01 - OBSIDIAN/06 - REPOSITORIOS VARIOS/erp-export-normalizer"
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
.venv/bin/erp-normalize --help
```

Loop verificación (corre tras cada cambio):
```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy cli.py core
.venv/bin/erp-normalize --schema core/formats/jde_ar.yaml --input examples/jde_ar.txt --output /tmp/out.json --format json
```

Exit codes `cli.py:40-43`: 0 ok / 1 runtime / 2 schema inválido / 3 errores validación.

## 3. Mapa rápido (dónde tocar)

| Archivo | Responsabilidad | Notas |
|---|---|---|
| `cli.py:157 _convert_file` | pipeline orquesta | parser → validator → rules → writer. No meter lógica negocio aquí |
| `core/schema.py` | carga/valida YAML | `start,length,type,format,scale,codepage,table,rules,parser` |
| `core/parser.py:15 FixedWidthReader.records()` | slicing bytes, streaming | |
| `core/converters.py:31 convert_field` | date/decimal/codepage | ver §5 P0-2 |
| `core/validator.py:54 validate_record` | errores acumulativos + `Stats` | |
| `core/writer.py:49 JsonWriter` etc. | 7 writers | ver §5 P0-1 |
| `core/detector.py:31 Detector.detect` | autodetect vs `core/formats/*.yaml` | ver §5 P0-3 |
| `core/generator.py` | inferencia schema delimitado | |
| `core/parallel.py:25 validate_parallel` | ProcessPool chunked, orden determinista | ver §5 P0-5 |
| `core/rules.py:32 RuleEngine` | sum/balance O(1) | ver §5 P0-4 |
| `core/audit.py:25 build_summary` | sidecar `.sha256` | |
| `core/io.py:21 read_erp()` | API lib Pandas/Polars/Spark | |
| `core/plugins.py:30 load_reader` | readers binarios custom | ejemplo `core/plugin_examples/length_prefixed_frame.py` |
| `core/copybook.py` | copybook COBOL → schema YAML | FD/01/05 + PIC, USAGE COMP-3/COMP |
| `core/crosscheck.py` | reconciliación entre archivos | sum/count/unique/missing, índice de claves |
| `core/diff.py` | diff por clave entre exports | added/removed/changed con detalle de campo |
| `core/formats/` | 9 YAML built-in: jde_ar/ap/gl/gl_distinct, sap_batch, sap_fi_document/bseg, cobol_fixed/packed | |
| `tests/` | 19 files | 217 OK, 1 skip pyspark |
| `docs/PLUGIN_API_v1.md` | contrato congelado del plugin `Reader` v1 (§4b) |
| `scripts/perf_profile.py` | perfil de rendimiento + gate de regresión | ver §4c |
| `examples/` | fixtures + README copy-paste | úsalo como fixtures |
| `docs/CONTEXTO.md`, `ARCHITECTURE.md`, `docs/TECHNICAL_DESIGN.md` | estado y diseño | lee CONTEXTO antes de planificar |

Docs fuente verdad: `README.md:5-40` propósito, `README.md:75-137` install/run, `README.md:268-278` test/lint.

**Credenciales de PyPI: §4.** Viven en el gestor de contraseñas del iPhone
del usuario, nunca en el repo. Si hay que publicar, se las pides a él.

## 4. Estado actual (2026-09-29, cierre de P1)

- META: aplicar beca `https://claude.com/contact-sales/claude-for-oss`.
- Tablero: `https://trello.com/b/c1hooM8i/erp-export-normalizer-seguimiento`.
- **P0-1..P0-5 + P1-1 + P1-2 + P1-3 ✅ cerrados** (ver tabla en `docs/CONTEXTO.md`).
  Plugin API v1 congelada en `docs/PLUGIN_API_v1.md` + 4 tests de contrato.
- **Ronda P1 alto valor ✅ cerrada** (6 tareas nuevas, ver §4b).
- Suite: **217 tests OK** (1 skip pyspark sin JVM), ruff y mypy limpios,
  `main` sincronizada con `origin/main`, CI verde en 3.10–3.13.
- **v0.2.0 publicada**: tag en git, release en GitHub y wheel en PyPI
  (`erp-export-normalizer==0.2.0`, verificado con `pip install` limpio).

### Credenciales de PyPI — dónde están

**El 2FA y el token de PyPI están en el gestor de contraseñas del iPhone del
usuario** (iCloud Keychain), no en este repositorio ni en este equipo.

Si hay que volver a publicar, **pídeselo al usuario**: el agente no tiene
acceso al iPhone y no debe inventar ni buscar credenciales.

```bash
# el usuario aporta el token; se pasa solo por variable de entorno,
# nunca se escribe en un archivo del repo ni en el historial del shell
TWINE_USERNAME="__token__" TWINE_PASSWORD="<token>" .venv/bin/twine upload dist/*
```

Token limitado al proyecto `erp-export-normalizer`. Si se filtra:
revocar en `https://pypi.org/manage/account/token/` y generar otro.

**Nunca** commitear `.pypirc`, `.env`, `*token*.txt`. Están en `.gitignore`
(`df6541f`) porque un `git add -A` los subía. El 2026-09-29 se subió una
0.2.0 desde `erp-export-token.txt` en la raíz; el archivo se borró tras
usarlo. No reintroducir esa práctica.

## 4b. Colas cerradas (2026-09-28/29) — no rehacer

Las tareas de §5 están **todas implementadas**. Detalle y commits:

| Tarea | Commit | Nota |
|---|---|---|
| P0-1 `JsonWriter` streaming | `a29334e` | `core/writer.py` escribe incremental, O(1) |
| P0-2 formatos fecha | `cc8ba37` | + `YYYY-MM-DD`, `DDMMYYYY`, `DD/MM/YYYY`, `YYMMDD` |
| P0-3 detector | `3b8056f` | muestra 20, scoring parcial, `None` si ambiguo |
| P0-4 rules | `1a12c66` | no-numérico = violación, spec validada en `__init__` |
| P0-5 `--chunk-lines` | `76a9bf7` | + `scripts/bench_parallel.py` |
| P1-1 Spark sin JVM | `3205589` | mock de `SparkSession` |
| P1-2 schemas | `6f559e5` | `sap_fi_bseg`, `jde_gl_distinct` |
| P1-3 errores UX | `f866fb1` | `field` + `raw` truncado a 50 chars |

Ronda P1 alto valor del 2026-09-29:

| Tarea | Commit | Nota |
|---|---|---|
| COBOL COMP-3 | `243fd62` | `type: packed`, BCD, no pasa por codepage |
| COPYBOOK import | `5f37738` | `erp-normalize copybook` → schema YAML |
| `crosscheck` | `c58fcfa` | sum/count/unique/missing entre archivos |
| `diff` | `db9fd94` | added/removed/changed por clave |
| Perf en CI | `bee92f1` | gate relativo + determinismo absoluto |
| Singer `STATE` | `15bbef1` | `--state` reanuda byte-idéntico |
| Release v0.2.0 | `24acb6b` | tag + notas + PyPI |

**Antes de tocar código, lee esta tabla y `git log`.** Si buscas trabajo nuevo,
las candidatas priorizadas están en `docs/CONTEXTO.md` §"Pasos a seguir".

## 4c. Trampas conocidas

`ruff format` **no** está en `pyproject.toml` como `exclude`; formatea también
los bloques `python` de los `.md`. Si `ruff format .` toca `docs/*.md` es
esperado. Ejecuta `ruff format .` y `ruff check --fix .` **antes** de commitear:
los commits P0/P1 dejaron 9 ficheros sin formatear y el job `lint` de CI
habría fallado.

**El gate de performance no puede comparar máquinas distintas.** El
`perf-baseline.json` está medido en el Mac del usuario, así que en el runner
de GitHub la firma de máquina no coincide y el gate **se salta** a propósito
(`62384a9`). No lo "arregles" subiendo la tolerancia: es hardware, no código.



## 5. Qué hacer (ordenado por impacto) — HISTÓRICO, TODO CERRADO

> Las 8 tareas de esta sección se implementaron el 2026-09-28/29. Se conservan
> como referencia de *cómo* se hizo y de los criterios de aceptación. **No las
> repitas**: ver la tabla de commits en §4b. Para trabajo nuevo, lee
> `docs/CONTEXTO.md` §"Pasos a seguir".

### P0-1 writer JSON rompe streaming — `core/writer.py:49-59`
Problema: `JsonWriter._rows: list[dict]` acumula todo en RAM. Promesa streaming rota en JSON grande. Resto de writers sí streamean (CSV/NDJSON/SQL por línea, Parquet batch 1000, Excel write-only).
Cómo:
1. Reescribe `JsonWriter` a streaming incremental: abre `[`, escribe cada row con `json.dump` + coma, cierra `]` en `finish()`. Maneja caso 0 rows → `[]`.
2. Ojo: `cli.py:235-236` llama `out.write(result)` por record — no cambiar firma `Writer` Protocol (`write(result)`, `finish()`).
3. Test: genera fixture 100k líneas (reusa `examples/jde_ar.txt` repetido), convierte a json con `/usr/bin/time -l` o `tracemalloc`, assert memoria acotada + output `json.load` válido + byte-idéntico a modo `--workers 1`.
Aceptación: `json` 1M rows sin OOM, tests viejos verdes.

### P0-2 fechas solo YYYYMMDD — `core/converters.py:45-57`
Problema: `convert_date` solo acepta `YYYYMMDD`, resto lanza `unsupported date format`.
Cómo:
1. Añade formatos: `YYYY-MM-DD, DDMMYYYY, DD/MM/YYYY, YYMMDD` mínimo. Mapea a `datetime.strptime` + retorna `.isoformat()`.
2. Actualiza `core/schema.py` validación de `format` permitido + 1 fixture por formato en `examples/`.
3. Tests en `tests/test_mvp.py` estilo existente: válido/inválido por formato.
Aceptación: 4 formatos nuevos con tests, error mensaje incluye formato esperado.

### P0-3 detector frágil — `core/detector.py:19,56-69`
Problema: `SAMPLE_RECORDS=5`, score = solo records perfectos (`PERFECT_RECORD_BONUS 10 + nfields`). Colisión fácil entre fixed-width misma longitud. Delimitados ni se scorean (`return 0` si `record_length is None`).
Cómo:
1. Sube muestra a 20 + scoring parcial: +1 por campo que convierte aunque el record falle, bonus perfecto como ahora.
2. Desempate: si `abs(score_a-score_b) < umbral`, retorna `None` + mensaje "ambiguo, pasa --schema" en vez de adivinar (cambia `detect()` en `core/detector.py:71`).
3. Añade test colisión: 2 schemas misma longitud, fixture ambiguo → `None`.
Aceptación: no falso positivo en colisión, `tests/test_formats.py` verde + nuevo test ambiguo.

### P0-4 rules ignora no-Decimal — `core/rules.py:42-45`
Problema: `_accumulate` solo suma `decimal.Decimal`. int/string se ignoran silencioso → `sum`/`balance` pasan cuando no deberían.
Cómo:
1. Si campo de la regla falta o no es Decimal/int/float → registra violación `rule 'sum:X': non-numeric` en vez de ignorar. Acepta int/float convirtiendo a Decimal.
2. Valida spec en `__init__`: `type in (sum,balance)`, keys requeridas, si no → `ValueError` temprano (falla en `load_schema`, no en `finalize`).
3. Tests: sum con string, balance con int, spec inválida.
Aceptación: 3 tests nuevos, ningún silencioso.

### P0-5 parallel overhead — `core/parallel.py:15,18-22`
Problema: `CHUNK_LINES=100k` + pickla `Schema`+bytes por chunk. Bien en GB, mal en medianos. Sin benchmark.
Cómo:
1. No cambies default aún. Añade `--chunk-lines` a `cli.py:85-90` (passthrough a `validate_parallel(..., chunk_lines)`), default `CHUNK_LINES`.
2. Benchmark script `scripts/bench_parallel.py`: archivo 50k/500k/2M líneas × workers 1/2/4 × chunk 10k/100k. Imprime tiempo + md5 output (debe ser idéntico).
3. Documenta resultado en `ARCHITECTURE.md` (tabla) y ajusta default solo si data lo pide.
Aceptación: flag funciona, outputs byte-idénticos, tabla bench en PR.

### P1-1 Spark skip — `tests/test_phase4.py`, `core/io.py`
Quita skip sin JVM: mockea `SparkSession` (fake `createDataFrame().collect()`), testea mapeo tipos sin JVM. No añadas dep pesada.

### P1-2 biblioteca formats (valor alto)
Añade 2-3 schemas reales con fixture + test detector: SAP FI BKPF/BSEG, JDE GL ya existe → añade AP con trailing-sign, COBOL con COMP-3 si hay demanda. Cada schema = `core/formats/*.yaml` + `examples/<nombre>.txt` + caso en `tests/test_formats.py`. Usa `erp-normalize registry <dir>` (`cli.py:339`) para validar.

### P1-3 UX errores
`validator.py` + `cli.py:266-267`: incluye `field` + valor raw truncado (50 chars) en `error_lines`. No rompas formato `line N:` (lo parsean scripts).

## 6. Cómo ampliar (recetas)

**Nuevo formato fixed-width:**
1. Copia `core/formats/jde_ar.yaml`, ajusta `record_length, fields[start,length,type]`.
2. Fixture en `examples/<nuevo>.txt` (5+ líneas, 1 con error intencional).
3. `erp-normalize registry core/formats` + `erp-normalize --schema ... --input ... --output /tmp/x.json --format json --dry-run --verbose`.
4. Test detector en `tests/test_formats.py`.

**Nuevo writer:** implementa `write(result)+finish()` en `core/writer.py`, registra en `make_writer:198`, añade a `choices` en `cli.py:81-84` y a `TEXT_FORMATS:45` si es texto (soporta `-` stdout). Test roundtrip en `tests/test_phase1.py` estilo.

**Nuevo tipo campo:** toca solo `core/converters.py:35 convert_text` + validación schema + tests. No toques parser.

**Nueva regla:** toca solo `core/rules.py:32-47 observe/finalize` + docs schema. Mantén O(1) memoria.

**Plugin binario:** crea `mi_parser.py` con `class Reader: def __init__(self,schema,path); def records(self,skip_first)->Iterator[(lineno,bytes)]`, referencia `parser: mi_parser:Reader` en YAML, pasa `--plugins-dir`. Ejemplo base: `core/plugin_examples/length_prefixed_frame.py`.

## 7. Reglas de trabajo (obligatorias)

- Commits conventional: `feat:`, `fix:`, `chore:`, `docs:`, `test:`. Un commit por paso lógico. Push tras cada paso.
- Antes de commit: `unittest discover + ruff check + ruff format --check + mypy cli.py core`.
- No añadas deps al core (hoy solo PyYAML). Opcionales van a `pyproject.toml:25-36 extras`.
- No toques `TEXT_FORMATS`/stdout sin test `-` en `cli.py:172-179`.
- Si cambias output, verifica determinismo: corre 2 veces + `md5`, y `--workers 1` vs `--workers 4` byte-idénticos.

## 8. Checklist entrega por tarea

- [ ] Código + test que falla sin fix y pasa con fix
- [ ] `unittest discover -s tests -v` verde
- [ ] `ruff check . && ruff format --check .` verde
- [ ] `mypy cli.py core` verde
- [ ] Smoke CLI con `examples/` (json + dry-run)
- [ ] Si cambia comportamiento: actualiza `README.md` + `docs/CONTEXTO.md` (sección Estado/Gaps)

## 9. Fuera de alcance (no hacer)

SaaS hosted, marketplace/registry centralizado, UI web completa. `core/webui.py` es solo preview local `127.0.0.1` — no autenticación, no multiusuario.

---
Generado 2026-09-28. Fuente: código + `docs/CONTEXTO.md` + análisis explorer exp-1. Si algo contradice al código, manda el código.
