# RFC: Restricciones por campo

- **Estado**: implementado
- **Fecha**: 2026-09-30
- **Relacionado**: tarjeta *Restricciones por campo* (P1)

## Problema

Hoy el validador solo comprueba una cosa: que un valor **se pueda convertir**
a su tipo declarado. Nada más. En un extracto contable eso deja fuera las
preguntas que de verdad importan:

- ¿el importe puede ser negativo?
- ¿la moneda está en la lista válida?
- ¿la fecha cae dentro del periodo?
- ¿el campo obligatorio vino vacío?

Un pipeline que acepta todo lo que se deja convertir está validando la
**forma**, no el **contenido**.

## La distinción que gobierna el diseño

Hay dos fallos muy distintos y el mensaje los tiene que distinguir:

| Tipo | Ejemplo | Significado |
|---|---|---|
| **Conversión** | `2024-13-45` en un campo `date` | El dato está **corrupto** |
| **Restricción** | `2024-13-01`... no; `2099-01-01` con `max: 2025-12-31` | El dato es **legible pero no permitido** |

El operador necesita la diferencia. "Formato inválido" dice que hay basura en
el fichero. "Fuera de rango" dice que el fichero está bien y lo que falla es
el negocio. Que el mensaje no lo distinga es un defecto del informe, no un
detalle de redacción: sin eso no se puede decidir si abrir una incidencia de
datos o una de configuración.

Por eso las restricciones se evalúan **después** de convertir, sobre el valor
ya tipado, y nunca mezcladas dentro del conversor.

## Restricciones

Todas opcionales, por campo, y validadas al cargar el schema.

```yaml
fields:
  - {name: amount, start: 33, length: 8, type: decimal, scale: 2, min: 0}
  - {name: currency, start: 41, length: 3, enum: [USD, EUR, ARS]}
  - {name: doc_no, start: 0, length: 10, required: true, pattern: '^[A-Z0-9]+$'}
  - {name: period, start: 20, length: 8, type: date, format: YYYYMMDD,
     min: 2024-01-01, max: 2025-12-31}
```

| Restricción | Tipos válidos | Semántica |
|---|---|---|
| `required` | todos | El campo no puede quedar vacío tras `trim` |
| `min` / `max` | `decimal`, `date` | Rango inclusivo sobre el valor tipado |
| `pattern` | `string` | Regex de Python, buscada sobre el valor **crudo** recortado |
| `enum` | todos | El valor debe estar en la lista |

### Decisiones deliberadas

**`required` mira el valor convertido, no los bytes.** Un campo `decimal`
todo relleno vale `0`, que no es `None`. `required` significa "este campo debe
traer información", así que se evalúa sobre el texto antes de convertir, con
la misma regla de `trim` que usa el resto. Es lo que el operador espera al
leer "obligatorio".

**`pattern` se busca sobre el texto, no sobre el tipo.** Aplicar una regex a
una fecha canónica es confundir las capas: el formato ya lo impose `format`.
`pattern` existe para strings.

**`enum` compara el valor convertido, no el texto.** `min`/`max` sobre
`date` comparan fechas ISO, no cadenas. Si `enum` comparara texto, `[1, 2, 3]`
no casaría con un `decimal` que llega como `1.00`.

**`min`/`max` en `date` aceptan `YYYY-MM-DD` o el `format` del campo.** El
operador escribe lo que lee, no el formato interno. Si el campo ya declara
`format: YYYYMMDD`, se acepta `2025-01-31`; si no, se exige ISO. Un
documento de negocio se escribe en ISO casi siempre, y obligar a recordar
el formato del campo sería una trampa.

**`min`/`max` en texto comparan sin distinguir mayúsculas.** Escrito a mano,
`min: a, max: z` quiere decir el abecedario, no el rango de puntos de código
donde `'M'` queda **por debajo** de `'a'` (en ASCII las mayúsculas van antes
que las minúsculas). Comparar con mayúsculas haría que ese rango pareciera
roto. Quien necesite distinguir mayúsculas de minúsculas lo expresa con
`case` en el campo, no con el rango.

**Una restricción mal escrita falla al cargar el schema**, no en la primera
fila. Un `pattern` que no compila, un `enum` vacío o un `min` mayor que el
`max` son errores de configuración y se dicen antes de leer un solo byte.

## Interacción con lo que ya existe

- **`trim`**: `required` y `pattern` respetan el recorte, para que un campo
  relleno no cuente como presente.
- **`mask`**: una restricción se evalúa sobre el valor **antes** de enmascarar.
  Enmascarar después de validar es lo correcto: si no, `required` vería `***`
  y `pattern` no encontraría nunca el patrón real.
- **`case`/`normalize`**: la comparación de `enum` es exacta, sobre el valor ya
  normalizado. Quien quiera insensibilidad a mayúsculas lo expresa con
  `case: upper` en el campo.
- **Errores**: la línea de reporte suma las violaciones de restricción con su
  propio texto (`field 'amount': below min 0`), sin tocar el prefijo
  `line N:` que los scripts ya parsean.

## Alternativas descartadas

| Opción | Por qué no |
|---|---|
| Expresiones libres (Lua, Python) | Un schema ejecutable convierte un fichero de datos en código; imposible de auditar |
| Validación por regex sobre el valor tipado | Obliga a conocer la serialización interna para escribir un patrón |
| Validar en el writer | El error aparecería a mitad de la escritura, cuando ya no se puede evitar |
| Un campo `rules` genérico | Se descarta por complejidad; `crosscheck` cubre lo que necesita dos ficheros |

## Lo que no cubre

- **Referencias entre ficheros**: eso es `crosscheck`, con su propia
  tarjeta (integridad referencial maestro-detalle).
- **Reglas de negocio sobre agregados**: eso es el bloque `rules` que ya
  existe (`sum`, `balance`).
- **Normalización previa**: `trim`/`case`/`normalize` ya son opciones de
  campo, y deben fijarse antes de confiar en una restricción.
