# Examples

Ready-to-run sample data + commands. Every fixture is generated from a
built-in schema and converts cleanly with zero config.

## JD Edwards AR (fixed-width, CP850)

```
erp-normalize --input data/jde_ar.txt --output - --format json
```

## SAP batch export (fixed-width, CP1252)

```
erp-normalize --input data/sap_batch.txt --output - --format csv
```

## COBOL export (EBCDIC!) 

Auto-detection picks the `cobol_fixed` schema — EBCDIC `cp037` decoding
happens transparently:

```
erp-normalize --input data/cobol.txt --output - --format ndjson
```

## Binary length-prefixed frames (plugin)

The bundled `length_prefixed_frame` plugin reads a binary frame stream
(2-byte big-endian length header + payload). Requires `--schema` since
binary formats cannot be auto-detected:

```
erp-normalize \
  --schema framed_schema.yaml \
  --input data/framed.bin \
  --output - \
  --format ndjson
```

## Try everything at once

```
erp-normalize --input data/jde_ar.txt  --output /tmp/jde.json    --format json
erp-normalize --input data/sap_batch.txt --output /tmp/sap.csv   --format csv
erp-normalize --input data/cobol.txt    --output /tmp/cobol.ndjson --format ndjson
erp-normalize --input data/framed.bin   --output /tmp/framed.json \
  --schema framed_schema.yaml
```

## Regenerating the fixtures

Fixtures were produced by the snippet below (run from this directory).
They are plain text (or a simple binary stream) — edit and re-run to make
your own.

```bash
python3 - <<'EOF'
import os, struct
base = "data"
os.makedirs(base, exist_ok=True)
def lj(s,n): return s[:n].ljust(n)
def rj(s,n): return s.rjust(n)

rows = [
    ("AR1001", "INVOICE", "20240815", rj("1234.56",8), "USD"),
    ("AR1002", "PAYMENT", "20240818", rj("2000.00",8), "USD"),
    ("AR1003", "CREDIT",  "20240822", rj("45.90",8),   "EUR"),
]
with open(os.path.join(base,"jde_ar.txt"),"wb") as f:
    for r in rows:
        line = lj(r[0],10)+lj(r[1],15)+r[2]+rj(r[3],8)+lj(r[4],3)
        f.write(line.encode("cp850")+b"\n")

sap = [
    ("1000000001","121000000001","20240901",rj("9876.54",15),"USD","  SALES"),
    ("1000000002","191000000005","20240903",rj("123.45",15),"EUR","  CLEAR"),
]
with open(os.path.join(base,"sap_batch.txt"),"wb") as f:
    for r in sap:
        line = lj(r[0],10)+lj(r[1],12)+r[2]+rj(r[3],15)+lj(r[4],5)+lj(r[5],10)
        f.write(line.encode("cp1252")+b"\n")

cobol = [
    ("CUST01","ACME INDUSTRIES CORP       ", rj("10450.25",12), "20240910", "A"),
    ("CUST02","GLOBEX TRADING SA          ", rj("78.00",12),    "20240912", "H"),
]
with open(os.path.join(base,"cobol.txt"),"wb") as f:
    for r in cobol:
        line = lj(r[0],6)+lj(r[1],30)+rj(r[2],12)+r[3]+r[4]
        f.write(line.encode("cp037")+b"\n")

def payload(i,d,a):
    return (i[:4].ljust(4)+d+a.rjust(8)).encode("ascii")
def framed(p): return struct.pack(">H",len(p))+p
with open(os.path.join(base,"framed.bin"),"wb") as f:
    for p in [payload("F001","20241001","99.95"), payload("F002","20241005","2500.00")]:
        f.write(framed(p))
EOF
```