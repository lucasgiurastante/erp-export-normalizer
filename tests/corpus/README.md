# Corpus de regresión de properties

`tests/test_properties.py` genera entradas aleatorias y afirma que ninguna
provoca un traceback. Cuando encuentra un fallo, hypothesis lo repite, pero
el fichero que lo contiene se queda en `.hypothesis/`, fuera del repo.

Cuando aparezca un fallo que merezca quedarse, hay que copiar **el ejemplo
concreto** a un test normal aquí dentro. Un caso aleatorio que se reproduce
no sirve de regresión: la próxima ejecución genera otra cosa y el fallo
vuelve a pasar desapercibido.

El formato es el de un `subTest`, para que se lea como un fichero de casos:

```python
class TestRegressions(unittest.TestCase):
    def test_record_that_crashed_conversion(self):
        cases = [b"\\x80\\x00\\x00", b"\\xff\\xfe"]
        for raw in cases:
            with self.subTest(raw=raw):
                ...
```

El caso que encontrado hypothesis el 2026-09-30: `b"\x80"` en un campo decimal
con codepage utf-8. `decode_field` usaba `errors="strict"` y soltaba un
`UnicodeDecodeError` crudo que tumbaba toda la conversion en vez de marcar un
campo. Corregido en `core/converters.py`.
