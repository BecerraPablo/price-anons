"""
Descarga el paquete diario de precios SEPA (Precios Claros) y genera
los datos para la página: solo la cadena y la localidad configuradas.
Por privacidad no escribe nombres de cadena, sucursal ni dirección en
el log ni en los datos publicados.

Salidas:
  docs/data/productos.json  -> foto del día (lo que lee el buscador)
  docs/data/historial.json  -> cambios de precio por producto

Configuración por variables de entorno (opcionales):
  CADENA     palabras a buscar en razón social o bandera, separadas por coma (obligatorio)
  LOCALIDAD  texto a buscar en la localidad o el nombre de la sucursal (obligatorio)
  SEPA_ZIP   ruta a un zip ya descargado (para pruebas locales, evita la descarga)

Solo usa la biblioteca estándar de Python.
"""
import csv
import io
import json
import re
import os
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone, timedelta

CKAN_URL = "https://datos.produccion.gob.ar/api/3/action/package_show?id=sepa-precios"
# Espejo público del mismo paquete (proyecto preciazo). Se usa si el portal oficial no responde,
# por ejemplo porque bloquea conexiones desde fuera de Argentina (como los servidores de GitHub).
ESPEJO_INDICE = "https://raw.githubusercontent.com/catdevnull/sepa-precios-metadata/main/index.json"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RAIZ, "docs", "data")
ART = timezone(timedelta(hours=-3))

CADENA = os.environ.get("CADENA", "")
LOCALIDAD = os.environ.get("LOCALIDAD", "")

UA = {"User-Agent": "price-anon/1.0 (+https://github.com/BecerraPablo/price-anon)"}


def norm(texto):
    """minúsculas, sin tildes, espacios simples"""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(t.lower().split())


def claves_cadena():
    return [norm(x) for x in CADENA.split(",") if norm(x)]


def patron_flexible(palabra):
    """regex que encuentra la palabra sin importar tildes ni mayúsculas"""
    eq = {"a": "aáàä", "e": "eéèë", "i": "iíìï", "o": "oóòö", "u": "uúùü", "n": "nñ"}
    return "".join(f"[{eq[c]}]" if c in eq else re.escape(c) for c in palabra)


def ocultar_cadena(texto):
    """reemplaza el nombre de la cadena (marca propia) por un genérico"""
    for k in claves_cadena():
        if len(k) >= 5 and " " not in k.strip():
            texto = re.sub(r"(?i)\b(la\s+)?" + patron_flexible(k) + r"\b", "MARCA PROPIA", texto)
    return texto


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- descarga

def curl(args, intentos=5, espera=15):
    """curl forzando IPv4 y con reintentos: el portal a veces deja colgada la conexión."""
    import subprocess, time
    ultimo = None
    for i in range(1, intentos + 1):
        r = subprocess.run(["curl", "--ipv4", "--fail", "--silent", "--show-error", "-L",
                            "-A", UA["User-Agent"]] + args, capture_output=True)
        if r.returncode == 0:
            return r.stdout
        ultimo = r.stderr.decode(errors="replace").strip()
        log(f"  intento {i}/{intentos} falló: {ultimo}")
        if i < intentos:
            time.sleep(espera)
    raise ConnectionError(ultimo)


def recurso_mas_reciente():
    cuerpo = curl(["--connect-timeout", "15", "--max-time", "40", CKAN_URL], intentos=2, espera=5)
    data = json.loads(cuerpo)
    recursos = [x for x in data["result"]["resources"] if str(x.get("url", "")).lower().endswith(".zip")]
    if not recursos:
        raise SystemExit("El dataset no tiene recursos .zip")

    import re

    def fecha(x):
        # la descripción dice p. ej. "Precios SEPA Minoristas miercoles, 2026-09-23"
        m = re.search(r"\d{4}-\d{2}-\d{2}", x.get("description") or "")
        return m.group(0) if m else (x.get("last_modified") or x.get("created") or "")[:10]

    recursos.sort(key=fecha, reverse=True)
    for x in recursos[:7]:
        log(f"  recurso: {fecha(x)}  {x.get('description') or x.get('name')}")
    return recursos[0], fecha(recursos[0])


def descargar(url, destino):
    log(f"Descargando {url}")
    curl(["--connect-timeout", "30", "--speed-limit", "1024", "--speed-time", "120",
          "--retry", "3", "--retry-all-errors", "--retry-delay", "15", "-o", destino, url], intentos=3, espera=30)
    log(f"  listo: {os.path.getsize(destino) >> 20} MB")


def desde_espejo(tmp):
    """Baja el paquete más reciente del espejo y lo extrae. Devuelve (carpeta, fecha, nombre)."""
    indice = json.loads(curl(["--connect-timeout", "20", "--max-time", "60", ESPEJO_INDICE], intentos=3, espera=10))
    for fecha in sorted(indice, reverse=True)[:3]:
        for rec in indice[fecha]:
            link = rec.get("link") or ""
            if not link.endswith(".tar.zst") or "mayorista" in link.lower():
                continue
            log(f"Espejo: {fecha}  {rec.get('name')}")
            archivo = os.path.join(tmp, "sepa.tar.zst")
            descargar(link, archivo)
            carpeta = os.path.join(tmp, "sepa")
            os.makedirs(carpeta, exist_ok=True)
            import subprocess
            r = subprocess.run(f'zstd -d --long=31 -c "{archivo}" | tar -x -C "{carpeta}"', shell=True)
            os.remove(archivo)
            if r.returncode != 0:
                log("  no pude descomprimir, pruebo el anterior")
                shutil.rmtree(carpeta, ignore_errors=True)
                continue
            return carpeta, fecha, rec.get("name") or link
    raise SystemExit("El espejo no tiene paquetes recientes")


class Carpeta:
    """Expone una carpeta con los CSV como si fuera un zip (namelist / open)."""
    def __init__(self, ruta):
        self.ruta = ruta
    def namelist(self):
        return [os.path.relpath(os.path.join(r, f), self.ruta) for r, _, fs in os.walk(self.ruta) for f in fs]
    def open(self, nombre):
        return open(os.path.join(self.ruta, nombre), "rb")


def recorrer_carpeta(raiz, tmp):
    carpetas, zips = [], []
    for r, _, fs in os.walk(raiz):
        if any(f.lower() == "comercio.csv" for f in fs):
            carpetas.append(r)
        zips += [os.path.join(r, f) for f in fs if f.lower().endswith(".zip")]
    log(f"  {len(carpetas)} carpetas con CSV y {len(zips)} zips en el paquete")
    for r in carpetas:
        c = Carpeta(r)
        try:
            ok, filas = es_la_cadena(c)
        except Exception as e:
            log(f"  no pude leer {os.path.relpath(r, raiz)}: {e}")
            continue
        if ok:
            log("  cadena encontrada")
            return procesar_cadena(c)
    for z in zips:
        try:
            res = recorrer(z, tmp)
        except zipfile.BadZipFile:
            log(f"  no es un zip válido, salteo: {os.path.relpath(z, raiz)}")
            continue
        if res:
            return res
    return None


# ---------------------------------------------------------------- lectura CSV

def leer_csv(zf, nombre):
    """Devuelve lista de dicts. Detecta separador y tolera BOM y líneas de pie."""
    with zf.open(nombre) as f:
        texto = io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace", newline="")
        primera = texto.readline()
        sep = max(["|", ";", ",", "\t"], key=primera.count)
        cab = [c.strip().lower() for c in primera.rstrip("\r\n").split(sep)]
        filas = []
        for partes in csv.reader(texto, delimiter=sep):
            if len(partes) < len(cab) - 2:  # pie tipo "Última actualización: ..."
                continue
            filas.append({cab[i]: (partes[i].strip() if i < len(partes) else "") for i in range(len(cab))})
    return cab, filas


def buscar_archivo(zf, base):
    for n in zf.namelist():
        if os.path.basename(n).lower() == base:
            return n
    return None


def col(fila, *nombres):
    for n in nombres:
        if n in fila and fila[n] != "":
            return fila[n]
    return ""


def numero(v):
    v = (v or "").strip()
    if not v:
        return None
    if "," in v and "." not in v:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def ean_valido(e):
    if not e.isdigit() or len(e) not in (8, 12, 13):
        return False
    if len(e) == 12:  # UPC-A -> EAN-13
        e = "0" + e
    if len(e) == 13:
        s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(e[:12]))
        return (10 - s % 10) % 10 == int(e[12])
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(e[:7]))
    return (10 - s % 10) % 10 == int(e[7])


# ---------------------------------------------------------------- proceso

def es_la_cadena(zf):
    n = buscar_archivo(zf, "comercio.csv")
    if not n:
        return False, []
    _, filas = leer_csv(zf, n)
    claves = claves_cadena()
    for f in filas:
        texto = norm(col(f, "comercio_razon_social") + " " + col(f, "comercio_bandera_nombre"))
        if any(k in texto for k in claves):
            return True, filas
    return False, filas


def procesar_cadena(zf):
    n_suc = buscar_archivo(zf, "sucursales.csv")
    n_prod = buscar_archivo(zf, "productos.csv")
    if not n_suc or not n_prod:
        raise SystemExit(f"Faltan CSV en el zip de la cadena: {zf.namelist()}")

    cab, sucs = leer_csv(zf, n_suc)
    log(f"  sucursales.csv columnas: {cab}")
    loc = norm(LOCALIDAD)
    elegidas = {}
    for s in sucs:
        texto = norm(" ".join([col(s, "sucursales_localidad"), col(s, "sucursales_nombre")]))
        if loc in texto:
            sid = col(s, "id_sucursal")
            elegidas[(col(s, "id_comercio"), col(s, "id_bandera"), sid)] = {
                "id": sid,
                "nombre": col(s, "sucursales_nombre"),
                "direccion": " ".join(x for x in [col(s, "sucursales_calle"), col(s, "sucursales_numero")] if x),
                "localidad": col(s, "sucursales_localidad"),
            }
    if not elegidas:
        raise SystemExit(f"No encontré sucursales con LOCALIDAD configurada ({len(sucs)} sucursales en la cadena). Revisá el secret LOCALIDAD.")
    log(f"  sucursales encontradas: {len(elegidas)}")
    # ids públicos opacos: 1, 2, ...
    alias = {k: str(i + 1) for i, k in enumerate(sorted(elegidas))}

    cab, filas = leer_csv(zf, n_prod)
    log(f"  productos.csv columnas: {cab}  filas totales: {len(filas)}")
    productos = []
    descartados = 0
    for f in filas:
        clave = (col(f, "id_comercio"), col(f, "id_bandera"), col(f, "id_sucursal"))
        if clave not in elegidas:
            continue
        ean = col(f, "id_producto").lstrip("0").zfill(13) if col(f, "id_producto").isdigit() else col(f, "id_producto")
        bandera_ean = col(f, "productos_ean")
        precio = numero(col(f, "productos_precio_lista"))
        if (bandera_ean and bandera_ean not in ("1", "true", "True")) or not ean_valido(ean) or not precio or precio <= 0:
            descartados += 1
            continue
        cant = numero(col(f, "productos_cantidad_presentacion"))
        uni = col(f, "productos_unidad_medida_presentacion")
        q = ""
        if cant:
            q = (f"{cant:g}" + (" " + uni.lower() if uni else "")).strip()
        promos = []
        for i in (1, 2):
            pp = numero(col(f, f"productos_precio_unitario_promo{i}"))
            ley = col(f, f"productos_leyenda_promo{i}")
            if pp or ley:
                promos.append([pp, ley])
        item = {
            "e": ean,
            "d": ocultar_cadena(col(f, "productos_descripcion")),
            "m": ocultar_cadena(col(f, "productos_marca")),
            "q": q,
            "p": round(precio, 2),
            "s": alias[clave],
        }
        ref = numero(col(f, "productos_precio_referencia"))
        if ref:
            item["r"] = [round(ref, 2), col(f, "productos_cantidad_referencia"), col(f, "productos_unidad_medida_referencia").lower()]
        if promos:
            item["o"] = promos
        productos.append(item)
    log(f"  productos de la sucursal: {len(productos)}  descartados: {descartados}")
    return [{"id": alias[k]} for k in sorted(elegidas)], productos


def recorrer(ruta_zip, tmp):
    """Recorre el zip de zips hasta encontrar la cadena."""
    with zipfile.ZipFile(ruta_zip) as z:
        # caso plano: los CSV están directamente en este zip
        if buscar_archivo(z, "comercio.csv"):
            ok, _ = es_la_cadena(z)
            if ok:
                return procesar_cadena(z)
        internos = [n for n in z.namelist() if n.lower().endswith(".zip")]
        log(f"  {len(internos)} zips internos en {os.path.basename(ruta_zip)}")
        for n in internos:
            destino = os.path.join(tmp, "interno.zip")
            with z.open(n) as src, open(destino, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            try:
                with zipfile.ZipFile(destino) as zi:
                    if buscar_archivo(zi, "comercio.csv"):
                        ok, filas = es_la_cadena(zi)
                        if ok:
                            log("  cadena encontrada")
                            return procesar_cadena(zi)
                        continue
                # zip dentro de zip dentro de zip
                sub = os.path.join(tmp, "sub")
                os.makedirs(sub, exist_ok=True)
                res = recorrer(destino, sub)
                if res:
                    return res
            except zipfile.BadZipFile:
                log(f"  zip dañado, salteo: {n}")
            finally:
                if os.path.exists(destino):
                    os.remove(destino)
    return None


def actualizar_historial(productos, fecha):
    ruta = os.path.join(DATA_DIR, "historial.json")
    hist = {}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            hist = json.load(f)
    cambios = 0
    for p in productos:
        k = f"{p['s']}-{p['e']}"
        serie = hist.setdefault(k, [])
        if serie and serie[-1][0] == fecha:
            serie[-1][1] = p["p"]
        elif not serie or serie[-1][1] != p["p"]:
            serie.append([fecha, p["p"]])
            cambios += 1
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  historial: {cambios} cambios de precio registrados")


def main():
    if not claves_cadena() or not norm(LOCALIDAD):
        raise SystemExit("Faltan los secrets CADENA y/o LOCALIDAD (Settings → Secrets and variables → Actions).")
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="sepa-")
    try:
        fecha_rec = ""
        if os.environ.get("SEPA_ZIP"):
            ruta = os.environ["SEPA_ZIP"]
            fuente = os.path.basename(ruta)
            res = recorrer_carpeta(ruta, tmp) if os.path.isdir(ruta) else recorrer(ruta, tmp)
        else:
            try:
                log("Consultando el portal oficial...")
                rec, fecha_rec = recurso_mas_reciente()
                ruta = os.path.join(tmp, "sepa.zip")
                descargar(rec["url"], ruta)
                fuente = rec.get("description") or rec.get("name") or rec["url"]
                res = recorrer(ruta, tmp)
            except ConnectionError as e:
                log(f"  el portal oficial no responde ({e}); uso el espejo")
                carpeta, fecha_rec, fuente = desde_espejo(tmp)
                fuente = "espejo: " + fuente
                res = recorrer_carpeta(carpeta, tmp)

        if not res:
            raise SystemExit("No encontré la cadena configurada en el paquete. Revisá el secret CADENA.")
        sucursales, productos = res
        if not productos:
            raise SystemExit("La sucursal no tiene productos en este paquete; no actualizo.")

        productos.sort(key=lambda p: norm(p["d"]))
        hoy = datetime.now(ART).strftime("%Y-%m-%d")
        if len(fecha_rec) == 10:
            hoy = fecha_rec  # fecha del dato, no de la corrida
        salida = {
            "fecha": hoy,
            "generado": datetime.now(ART).isoformat(timespec="minutes"),
            "fuente": "espejo" if str(fuente).startswith("espejo") else "portal",
            "sucursales": sucursales,
            "productos": productos,
        }
        with open(os.path.join(DATA_DIR, "productos.json"), "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
        actualizar_historial(productos, hoy)
        log(f"OK: {len(productos)} productos guardados ({hoy})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
