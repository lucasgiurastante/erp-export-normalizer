# Preguntas para la comunidad SAP

## Por qué preguntas y no un post de producto

Un post que anuncia una herramienta se lee como eso. Una pregunta que
apunta a un hueco real del ecosistema trae respuestas, y las respuestas
dicen qué construir.

Además hay una razón práctica: la comunidad de SAP tiene una regla
explícita contra llevar tráfico a sitios externos, y otra específica sobre
contenido generado con IA. Preguntar es siempre permitido. Enlazar fuera, casi
nunca. Los textos de aquí no enlazan nada.

## Antes de publicar

**Espera a tener karme.** Los moderadores miran el historial. Una cuenta
nueva con un post técnico se lee como un bot. Un mes participando en comentarios
cambia la recepción por completo, y además te dice qué preguntar antes.

**No hace falta cuenta nueva para esto.** Si tienes una cuenta con
historial, úsala. Si no, crea una y spends dos semanas en comentarios.

**Si alguien te pregunta qué haces, contéstalo.** Seamos claros: si mantienes
una herramienta para leer estos ficheros y preguntas en detalle sobre cómo se
leen, alguien lo va a averiguar, y descubrirlo después es bastante peor que
decirlo. La forma que funciona es en el primer párrafo o en el primer
comentario: "I work on something in this space, so I'm biased." No hace falta
enlazarlo. La transparencia sube la recepción; el secreto la hunde.

---

## Pregunta 1: las convenciones de signo

Esta es la más valiosa de las tres, porque puede contradecir lo que acabo de
implementar y prefiero enterarme antes que después.

> **How do you read the sign on an amount coming out of SAP?**
>
> I'm working through FI document extracts and I keep getting stuck on the
> same thing. `DMBTR` and `WRBTR` are `CURR(13,2)`, so the amount arrives
> unsigned with an implied decimal, and the sign lives in a separate field,
> `SHKZG`, where S is debit and H is credit.
>
> Three things I would like to hear how people actually handle this,
> rather than how the data dictionary says to:
>
> 1. When you load a BSEG extract into a reporting tool or a spreadsheet, do
>    you keep `SHKZG` as a separate column and let the consumer apply it, or
>    do you fold it into a signed number at load time and drop the column?
>    I've seen both and I'm not sure which survives an audit better.
>
> 2. On the implied decimal, do your tools read `0000001234560` as 12345.60
>    directly from the 13 digits, or do you expect a point to be present and
>    treat a digit-only value as an error? I've hit both conventions in
>    files that were supposedly the same export.
>
> 3. Where does a trailing sign ( `1234.56-` or `1234.56DB` ) show up in
>    your world? I keep assuming it's a JDE-ism, but I've also seen it
>    described as a mainframe convention, and I'm not sure whether to expect
>    it from an SAP-side export at all.

---

## Pregunta 2: cabecera y detalle en el mismo fichero

Esta ya tiene demanda documentada: hay hilos de 2013 en la comunidad
preguntando exactamente esto, sin respuesta satisfactoria.

> **How do you handle a flat file where header and line item records have
> different structures?**
>
> We get FI extracts where the same file carries a header record, then N
> line items, then the next header. The records are distinguished by a type
> field, but the fields after it are not the same width in both cases.
>
> What I would like to know is what the practical options are, and what
> people actually end up doing:
>
> 1. Do you split the file first by record type and process two structures
>    separately, or do you parse it in one pass with a per-record-type
>    layout?
>
> 2. If the record has no reliable type marker, is prefix matching the normal
>    approach, and how do you deal with the line number field that some
>    exports have and others don't?
>
> 3. Does anyone do this in the database instead, loading everything as text
>    and reshaping afterwards? I find that appealing because it avoids
>    committing to a layout, but I have no idea how it performs on a 5 GB
>    file.
>
> For context on where I'm coming from: the piece I find genuinely hard is
> that the answer seems to change per client, and the layout lives in
> whatever the customer's ABAP wrote years ago.

---

## Pregunta 3: la extracción que llega vacía

Esta es de operaciones, y es donde más euros se pierden. Es
exactamente la decisión que tomé con `--fail-on-empty`, así que me interesa
saber si la tomé bien.

> **What do you do when a scheduled extract comes back with zero rows?**
>
> We have nightly files from a legacy system that feed a reporting load. The
> failure mode that worries me is not a parse error, it's a successful run
> that produces nothing: the feed breaks upstream, the file arrives with a
> header and no data, and everything downstream is quietly wrong for a week
> until someone reconciles by hand.
>
> What I would like to hear:
>
> 1. Do you fail the job, or do you let it through and alert separately?
>    Right now I have it as an opt-in flag that exits non-zero, on the
>    assumption that a silent zero is the worst outcome.
>
> 2. For those of you who do fail, how do you avoid the false alarm on a
>    day that genuinely has no transactions? Weekend and holiday closures
>    exist, and so do batch windows that legitimately produce nothing.
>
> 3. What do you compare against to know the file is right when it does
>    have rows? Row count, a control total on an amount column, a hash of
>    the file, or a previous file for reference?

That last question is the one I care about most. A hash tells you the file
changed, and a row count tells you roughly how much, but neither tells you
the layout did not shift underneath you while the count stayed plausible.

---

## Dónde publicarlas

| Dónde | Cómo |
|---|---|
| `community.sap.com` | Categoría *ABAP Development* o *Application Development and Automation*. Son preguntas legitimas sobre el ecosistema. Sin enlaces externos. |
| `r/SAP` | Threads de migración y extracciones. Verifica el sidebar antes, cada subreddit tiene sus reglas. |
| `r/ABAP` | Más técnico.Buen sitio para las preguntas 1 y 2. |
| Stack Overflow | La pregunta 1 tiene buen sitio de pregunta bien escrita, con tags `sap`, `abap`, `bseg`. |

No publiques las tres en el mismo día ni en el mismo sitio. Una por semana
como mucho, y responde a lo que digan, que es la parte que construye el
historial.

## Qué hacer con las respuestas

Cada respuesta es un requisito o un schema. Anótalos en el tablero antes de
que se pierdan, que las threads de la comunidad se entierran rápido:

- Si alguien dice que confía en un total de control, eso es una regla
  `sum` y ya la hay.
- Si alguien describe un esquema de cabecera/detalle con un campo de tipo
  fiable, la tarjeta *Ficheros cabecera + detalle* deja de ser especulativa
  y se puede implementar.
- Si alguien dice que aplica el signo en la carga y descarta `SHKZG`, eso
  significa que mi BSEG debería derivar el importe y no solo exponer la
  columna. Es un cambio de schema.
