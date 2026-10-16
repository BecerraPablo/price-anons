"""
Diagnóstico: ¿qué productos que faltan en la sucursal propia tienen otras
sucursales de la misma cadena, y a qué distancia?

Solo escribe distancias y cantidades (sin nombres ni localidades) en
docs/data/diagnostico-sucursales.txt.

Variables: CADENA, LOCALIDAD (secrets), EANS (opcional, separados por coma).
"""
import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actualizar as A  # noqa: E402

SALIDA = os.path.join(A.DATA_DIR, "diagnostico-sucursales.txt")


def km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def carpeta_cadena(raiz):
    for r, _, fs in os.walk(raiz):
        if any(f.lower() == "comercio.csv" for f in fs):
            c = A.Carpeta(r)
            try:
                ok, _ = A.es_la_cadena(c)
            except Exception:
                continue
            if ok:
                return c
    return None


def main():
    eans_pedidos = [e.strip() for e in os.environ.get("EANS", "").split(",") if e.strip()]
    tmp = tempfile.mkdtemp(prefix="diag-")
    lineas = []
    try:
        carpeta, fecha, _ = A.desde_espejo(tmp)
        c = carpeta_cadena(carpeta)
        if not c:
            raise SystemExit("No encontré la cadena configurada")

        _, sucs = A.leer_csv(c, A.buscar_archivo(c, "sucursales.csv"))
        loc = A.norm(A.LOCALIDAD)
        coords, propia = {}, None
        for s in sucs:
            clave = (A.col(s, "id_comercio"), A.col(s, "id_bandera"), A.col(s, "id_sucursal"))
            la, lo = A.numero(A.col(s, "sucursales_latitud")), A.numero(A.col(s, "sucursales_longitud"))
            if la and lo:
                coords[clave] = (la, lo)
            if loc in A.norm(A.col(s, "sucursales_localidad") + " " + A.col(s, "sucursales_nombre")):
                propia = clave
        if not propia or propia not in coords:
            raise SystemExit("No encontré la sucursal propia o no tiene coordenadas")
        dist = {k: km(coords[propia], v) for k, v in coords.items()}

        # productos por sucursal (solo EAN válidos)
        _, filas = A.leer_csv(c, A.buscar_archivo(c, "productos.csv"))
        por_suc, precios = {}, {}
        crudo_propia = set()
        for f in filas:
            clave = (A.col(f, "id_comercio"), A.col(f, "id_bandera"), A.col(f, "id_sucursal"))
            pid = A.col(f, "id_producto")
            ean = pid.lstrip("0").zfill(13) if pid.isdigit() else pid
            if clave == propia:
                crudo_propia.add(ean)
            if not A.ean_valido(ean):
                continue
            por_suc.setdefault(clave, set()).add(ean)
            precios[(clave, ean)] = A.numero(A.col(f, "productos_precio_lista"))
        propios = por_suc.get(propia, set())

        lineas.append(f"Diagnóstico de sucursales · datos del {fecha}")
        lineas.append(f"Sucursales de la cadena con coordenadas: {len(coords)}")
        lineas.append(f"Productos con EAN válido en la sucursal propia: {len(propios)}")
        lineas.append("")
        lineas.append("Sucursales más cercanas (km · productos · productos que la propia no tiene):")
        cercanas = sorted((d, k) for k, d in dist.items() if k != propia)[:8]
        for d, k in cercanas:
            todos = por_suc.get(k, set())
            lineas.append(f"  {d:6.0f} km · {len(todos):5d} · {len(todos - propios):5d}")
        lineas.append("")
        lineas.append("Productos extra que aportaría sumar sucursales dentro de cada radio:")
        for radio in (30, 60, 100, 150, 250):
            extra = set()
            n = 0
            for k, d in dist.items():
                if k != propia and d <= radio:
                    extra |= por_suc.get(k, set()) - propios
                    n += 1
            lineas.append(f"  hasta {radio:3d} km: {n:3d} sucursales · {len(extra):5d} productos extra")

        for e in eans_pedidos:
            e13 = e.lstrip("0").zfill(13)
            lineas.append("")
            lineas.append(f"Código {e13}:")
            lineas.append(f"  en la sucursal propia: {'sí' if e13 in propios else ('figura pero descartado' if e13 in crudo_propia else 'no')}")
            donde = sorted((dist[k], precios.get((k, e13))) for k, s in por_suc.items() if e13 in s and k in dist and k != propia)
            if donde:
                lineas.append(f"  en {len(donde)} otras sucursales; las más cercanas:")
                for d, p in donde[:5]:
                    lineas.append(f"    {d:6.0f} km · ${p:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            else:
                lineas.append("  no figura en ninguna sucursal de la cadena")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    texto = "\n".join(lineas) + "\n"
    print(texto)
    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(texto)


if __name__ == "__main__":
    main()
