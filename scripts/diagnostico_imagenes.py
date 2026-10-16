"""
Diagnóstico de cobertura de imágenes por EAN.

Toma una muestra al azar de docs/data/productos.json y prueba cada fuente.
Escribe solo porcentajes en docs/data/diagnostico-imagenes.txt.

Variables opcionales: MUESTRA (default 300), SEMILLA (default 1).
"""
import json
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RAIZ, "docs", "data")
UA = "Mozilla/5.0 (compatible; price-anons/1.0)"
TIMEOUT = 12

# Tiendas con catálogo VTEX: búsqueda pública por EAN
VTEX = {
    "carrefour": "https://www.carrefour.com.ar",
    "jumbo": "https://www.jumbo.com.ar",
    "vea": "https://www.vea.com.ar",
    "disco": "https://www.disco.com.ar",
    "dia": "https://diaonline.supermercadosdia.com.ar",
    "changomas": "https://www.masonline.com.ar",
}


def get(url, cabeza=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}, method="HEAD" if cabeza else "GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.headers.get("Content-Type", ""), (b"" if cabeza else r.read()), r.headers.get("Content-Length")


def preciosclaros(ean):
    st, ct, _, largo = get(f"https://imagenes.preciosclaros.gob.ar/productos/{ean}.jpg", cabeza=True)
    return st == 200 and ct.startswith("image") and (largo is None or int(largo) > 1500)


def openfoodfacts(ean):
    st, _, body, _ = get(f"https://world.openfoodfacts.org/api/v2/product/{ean}?fields=image_front_small_url")
    return st == 200 and bool(json.loads(body).get("product", {}).get("image_front_small_url"))


def vtex(base):
    def probar(ean):
        st, _, body, _ = get(f"{base}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}")
        if st not in (200, 206):
            return False
        prods = json.loads(body)
        return any(img.get("imageUrl") for p in prods for it in p.get("items", []) for img in it.get("images", []))
    return probar


FUENTES = {"preciosclaros": preciosclaros, "openfoodfacts": openfoodfacts}
FUENTES.update({k: vtex(v) for k, v in VTEX.items()})


def medir(nombre, fn, eans):
    ok, errores, ultimo_error = set(), 0, ""
    t0 = time.time()

    def uno(e):
        try:
            return e, fn(e), None
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError) as ex:
            return e, False, f"{type(ex).__name__}: {getattr(ex, 'code', '') or getattr(ex, 'reason', '') or ex}"

    with ThreadPoolExecutor(max_workers=6) as ex:
        for e, encontrado, err in ex.map(uno, eans):
            if encontrado:
                ok.add(e)
            if err:
                errores += 1
                ultimo_error = err
    return ok, errores, ultimo_error, time.time() - t0


def main():
    n = int(os.environ.get("MUESTRA", "300"))
    random.seed(int(os.environ.get("SEMILLA", "1")))
    with open(os.path.join(DATA, "productos.json"), encoding="utf-8") as f:
        productos = json.load(f)["productos"]
    eans = random.sample([p["e"] for p in productos], min(n, len(productos)))

    lineas = [f"Diagnóstico de imágenes · muestra de {len(eans)} productos de {len(productos)}", ""]
    lineas.append(f"{'fuente':<15}{'con foto':>10}{'errores':>10}{'segundos':>10}  último error")
    resultados = {}
    for nombre, fn in FUENTES.items():
        ok, err, ult, seg = medir(nombre, fn, eans)
        resultados[nombre] = ok
        linea = f"{nombre:<15}{len(ok) / len(eans):>9.0%}{err:>10}{seg:>10.0f}  {ult[:80]}"
        print(linea, flush=True)
        lineas.append(linea)

    # cobertura acumulada: agregando fuentes en orden de mejor a peor
    lineas += ["", "Cobertura acumulada (sumando fuentes de mayor a menor):"]
    cubiertos = set()
    for nombre in sorted(resultados, key=lambda k: len(resultados[k]), reverse=True):
        cubiertos |= resultados[nombre]
        lineas.append(f"  + {nombre:<15}{len(cubiertos) / len(eans):>6.0%}")
    lineas.append(f"\nSin foto en ninguna fuente: {len(eans) - len(cubiertos)} de {len(eans)}")

    texto = "\n".join(lineas) + "\n"
    print(texto)
    with open(os.path.join(DATA, "diagnostico-imagenes.txt"), "w", encoding="utf-8") as f:
        f.write(texto)


if __name__ == "__main__":
    main()
