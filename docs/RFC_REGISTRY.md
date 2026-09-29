# RFC: Registry público de schemas versionado

- **Estado**: propuesta, prototipo local implementado
- **Fecha**: 2026-09-29
- **Autor**: maintainer de `erp-export-normalizer`
- **Implementa**: tarjeta *Registry público versionado* (P2)
- **Relacionado**: §9 del instructivo (registry centralizado = fuera de
  alcance) — este RFC **no** contradice esa decisión: define el formato y un
  prototipo local, y deja explícito qué haría falta para hacerlo público.

## Problema

El conocimiento del layout de un archivo ERP hoy vive en un YAML dentro de
cada repositorio. Consecuencias:

1. **Se reescribe a mano.** Cada proyecto vuelve a teclear offsets que ya
   existían en otro proyecto.
2. **No se puede verificar.** No hay forma de saber si un schema que
   descargaste es el que crees, ni de saber si cambió por debajo.
3. **No se puede compartir con seguridad.** Un schema es código que decide
   dónde empieza un número de cuenta. Aceptarlo sin más es aceptar una
   entrada mal formada (o malintencionada) en un pipeline financiero.

La tarjeta de P2 lo pedía así: *"Schemas firmados con checksum + `erp-normalize
search`. Evidencia de comunidad. Requiere diseño versionado antes de código."*

## Objetivos

- Un **formato de índice** estable, versionado y legible sin este repo.
- **Integridad verificable offline**: un checksum en el manifiesto permite
  confirmar que un schema descargado es el publicado.
- **Búsqueda local**: encontrar un schema por nombre, sistema o descripción.
- **Sin red en el prototipo**: la herramienta sigue siendo air-gapped.

## No objetivos

- Un servidor central. Eso queda para §"Hacia un registry público" más abajo.
- Firma criptográfica (GPG/minisign). El checksum detecta corrupción y
  cambios accidentales, **no** a un atacante con acceso al índice. Decirlo
  aquí evita que alguien confíe en más de lo que el diseño da.
- Resolución automática de schemas privados de cliente.

## Formato del manifiesto

Un índice es un YAML (JSON válido también, se carga como YAML):

```yaml
registry: 1          # versión del FORMATO del índice, no del schema
updated: 2026-09-29
schemas:
  - name: jde_ar
    path: core/formats/jde_ar.yaml
    version: 1.0.0        # versión del SCHEMA (semver)
    system: JD Edwards
    description: Accounts Receivable export
    sha256: 1a2b3c...     # checksum del archivo referenciado
    tags: [jde, ar, fixed-width]
```

Reglas:

| Regla | Motivo |
|---|---|
| `registry` es entero y va primero | Un lector viejo debe poder rechazar un formato nuevo en vez de interpretarlo mal |
| `version` es semver del **schema** | Un schema `2.0.0` puede romper el layout; el índice no es quien lo decide |
| `sha256` es obligatorio | Sin checksum no hay verificación; el índice lo declara inválido |
| `name` es único y estable | Es la clave de `--registry-get name`; renombrar rompe scripts |
| `tags` es una lista de strings | Búsqueda por etiqueta, sin dependencia externa |

## Versionado de schemas

Igual que el contrato de plugins (`docs/PLUGIN_API_v1.md`):

- **patch**: comentario, `description`. Mismo layout.
- **minor**: un schema nuevo, un campo opcional con default. Archivos que
  convertían antes siguen convirtiendo igual.
- **major**: cambia un `start`, un `length`, un `type` o `record_length`.
  Puede cambiar la salida de una fila válida.

El índice registra la versión; **quien actualiza decide el salto**. Nada
impone un major automáticamente, porque "qué es compatible" depende del
cliente, no del editor del índice.

## Búsqueda

`erp-normalize registry search <término>` sobre índices locales. El término
se busca en `name`, `system`, `description` y `tags`, sin distinguir
mayúsculas, con coincidencia de subcadena. Un término vacío lista todo.

Un solo índice o varios: se acepta una lista de rutas y se buscan en todos,
marcando la fuente de cada resultado.

## Verificación

`erp-normalize registry verify <índice> <directorio>`:

1. Cada entrada debe tener `name`, `version`, `path` y `sha256`.
2. El archivo referenciado debe existir.
3. Su SHA-256 debe coincidir con el declarado.
4. El schema debe pasar `build_schema` (offsets válidos, tipos soportados).

Sale con `3` si algo no cuadra, con el detalle por entrada.

## Prototipo implementado (local, sin red)

- `core/registry.py`: carga y valida índices, busca, verifica checksums y
  valida schemas. Todo con la stdlib + PyYAML, igual que el resto.
- `erp-normalize registry search|verify|index`: subcomandos.
- El repo trae `registry.yaml` con sus 9 schemas built-in y sus checksums
  reales, de modo que `registry verify` se puede ejecutar hoy mismo.
- `tests/test_registry.py`: formato inválido, checksum incorrecto, schema
  que no carga, búsqueda y truncado.

Nada de esto sale a la red. Sigue cumpliéndose la regla de diseño: *no
network, no database, no hidden state*.

## Hacia un registry público (fuera de alcance ahora)

Lo que faltaría, y por qué no se hace todavía:

- **Dónde vive el índice.** Un repositorio git público es lo más simple y
  auditable; un servicio con API añade una dependencia que el proyecto
  explícitamente evita.
- **Autenticidad.** Un checksum en un índice remoto no protege: quien
  publica el índice puede cambiar el checksum. Hace falta firma
  (minisign/GPG) y una raíz de confianza documentada. Es un problema de
  claves, no de formato.
- **Moderación.** Un schema público define dónde acaba un importe. Sin
  revisión, el registro se llena de entradas que rompen pipelines. Con
  revisión, se necesita alguien que la haga.
- **Versionado del índice en la práctica.** Hace falta decidir quién
  publica un `registry: 2` y qué deja de aceptar.

Mientras no haya cliente que lo pida, un YAML con checksums y un comando
`search` cubre el 80% del valor sin añadir superficie de ataque.

## Alternativas descartadas

| Opción | Por qué no |
|---|---|
| Schemas en PyPI como paquetes | Empaquetar 30 líneas de YAML en un wheel es absurdo; los releases se multiplican |
| Registro por URL en el schema | Rompe el airegap y hace la conversión no determinista |
| Confiar en el nombre del archivo | No detecta contenido alterado, solo renombrado |
| Base de datos central | Contradice *no database* y añade una dependencia permanente |
