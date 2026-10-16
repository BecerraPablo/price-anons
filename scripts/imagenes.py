"""
Busca la foto de cada producto por EAN y guarda los enlaces en docs/data/imagenes.json.

Fuentes, en orden: Carrefour, Jumbo, Changomás (catálogos VTEX) y Precios Claros.
Todas las fuentes son catálogos argentinos. Cada foto se vuelve a buscar cada
REFRESCO_DIAS días (por cambios de empaque); la renovación se reparte para que cada
día se refresque más o menos la misma cantidad. Los productos sin foto se reintentan
con la misma frecuencia. Si una búsqueda falla por la red, se reintenta al día siguiente.

Formato de imagenes.json (compacto para que la página cargue rápido):
  {"p": ["https://.../arquivos/ids/", ...],              prefijos de URL
   "i": {"<ean>": [indice_prefijo, "resto", "fecha"]},   foto y día en que se buscó
   "f": {"<ean>": "AAAA-MM-DD"}}                         última búsqueda sin éxito
"""
import json
import os
import re
import time
import zlib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RAIZ, "docs", "data")
RUTA = os.path.join(DATA, "imagenes.json")
UA = "Mozilla/5.0 (compatible; price-anons/1.0)"
TIMEOUT = 12
REFRESCO_DIAS = int(os.environ.get("REFRESCO_DIAS", "15"))
MAX_BUSQUEDAS = int(os.environ.get("MAX_BUSQUEDAS", "7000"))
HOY = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")

VTEX = [
    ("carrefour", "https://www.carrefour.com.ar"),
    ("jumbo", "https://www.jumbo.com.ar"),
    ("changomas", "https://www.masonline.com.ar"),
]
PC = "https://imagenes.preciosclaros.gob.ar/productos/"


def get(url, cabeza=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}, method="HEAD" if cabeza else "GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.headers.get("Content-Type", ""), (b"" if cabeza else r.read())


def foto_vtex(base, ean):
    st, _, body = get(f"{base}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}")
    if st not in (200, 206):
        return None
    for p in json.loads(body):
        for it in p.get("items", []):
            if it.get("ean") and it["ean"].lstrip("0") != ean.lstrip("0"):
                continue
            for img in it.get("images", []):
                url = img.get("imageUrl")
                if url:
                    # miniatura de 300x300 en vez de la foto original
                    return re.sub(r"/ids/(\d+)(-\d+-\d+)?/", r"/ids/\1-300-300/", url.split("?")[0])
    return None


def foto_pc(ean):
    st, ct, _ = get(f"{PC}{ean}.jpg", cabeza=True)
    return f"{PC}{ean}.jpg" if st == 200 and ct.startswith("image") else None


def buscar(ean):
    errores = 0
    for _, base in VTEX:
        try:
            url = foto_vtex(base, ean)
            if url:
                return ean, url, errores
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            errores += 1
    try:
        return ean, foto_pc(ean), errores
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return ean, None, errores + 1


def partir(url):
    """separa la URL en prefijo reutilizable y resto"""
    for marca in ("/arquivos/ids/", "/productos/"):
        if marca in url:
            i = url.index(marca) + len(marca)
            return url[:i], url[i:]
    return "", url


def dias_atras(n):
    return (datetime.strptime(HOY, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


def main():
    with open(os.path.join(DATA, "productos.json"), encoding="utf-8") as f:
        eans = sorted({p["e"] for p in json.load(f)["productos"]})

    datos = {"p": [], "i": {}, "f": {}}
    if os.path.exists(RUTA):
        with open(RUTA, encoding="utf-8") as f:
            datos = json.load(f)
    prefijos = datos["p"]

    # fotos guardadas sin fecha: se les asigna una escalonada para repartir el refresco
    for e, v in datos["i"].items():
        if len(v) < 3:
            v.append(dias_atras(zlib.crc32(e.encode()) % REFRESCO_DIAS))

    vence = dias_atras(REFRESCO_DIAS)
    nuevos = [e for e in eans if e not in datos["i"] and datos["f"].get(e, "0000") <= vence]
    refrescar = sorted((e for e in eans if e in datos["i"] and datos["i"][e][2] <= vence),
                       key=lambda e: datos["i"][e][2])
    pendientes = (nuevos + refrescar)[:MAX_BUSQUEDAS]
    print(f"Productos: {len(eans)} · con foto: {sum(e in datos['i'] for e in eans)} · "
          f"a buscar hoy: {len(nuevos)} sin foto + {len(refrescar)} para refrescar", flush=True)

    encontrados, cambiadas, errores, t0 = 0, 0, 0, time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for n, (ean, url, err) in enumerate(ex.map(buscar, pendientes), 1):
            errores += err
            previa = datos["i"].get(ean)
            if url:
                pre, resto = partir(url)
                if pre not in prefijos:
                    prefijos.append(pre)
                nueva = [prefijos.index(pre), resto, HOY]
                if previa and previa[:2] != nueva[:2]:
                    cambiadas += 1
                datos["i"][ean] = nueva
                datos["f"].pop(ean, None)
                encontrados += 1
            elif err:
                pass  # error de red: se reintenta en la próxima corrida
            elif previa:
                previa[2] = HOY  # ya no figura en las fuentes: se conserva la foto anterior
            else:
                datos["f"][ean] = HOY
            if n % 500 == 0:
                print(f"  {n}/{len(pendientes)} · encontradas {encontrados}", flush=True)

    # no guardar productos que ya no están en la sucursal
    vigentes = set(eans)
    datos["i"] = {e: v for e, v in datos["i"].items() if e in vigentes}
    datos["f"] = {e: v for e, v in datos["f"].items() if e in vigentes}
    with open(RUTA, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    con = sum(e in datos["i"] for e in eans)
    print(f"Hoy: {encontrados} encontradas de {len(pendientes)} búsquedas, {cambiadas} fotos cambiaron "
          f"({time.time() - t0:.0f} s, {errores} errores de red)")
    print(f"Cobertura total: {con} de {len(eans)} ({con / max(1, len(eans)):.0%})")


if __name__ == "__main__":
    main()
