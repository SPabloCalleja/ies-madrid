"""
Paso 2a (v6): descargar, VALIDAR y APLANAR los 5 GTFS + el callejero OSM.
v6: si el fichero -latest de Geofabrik falla (ahora mismo redirige a una URL
rota), raspa la pagina de la region y baja el snapshot FECHADO mas reciente
(p.ej. madrid-261115.osm.pbf). Para OTP es equivalente: el callejero no cambia
en dias; lo que caduca son los GTFS.

Uso:
    python descargar_transportes.py                # todo
    python descargar_transportes.py interurbanos   # solo ese feed (metro|emt|interurbanos|ligero|cercanias|osm)
"""
import re
import sys
import io
import csv
import os
import zipfile
import requests

OBLIGATORIOS = ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]

FEEDS = {
    "metro": ("gtfs_metro.zip", [
        "https://crtm.maps.arcgis.com/sharing/rest/content/items/5c7f2951962540d69ffe8f640d94c246/data",
        "https://www.arcgis.com/sharing/rest/content/items/5c7f2951962540d69ffe8f640d94c246/data",
    ]),
    "emt": ("gtfs_emt.zip", [
        "https://servicios.emtmadrid.es:8443/gtfs/transitemt.zip",
        "https://crtm.maps.arcgis.com/sharing/rest/content/items/868df0e58fca47e79b942902dffd7da0/data",
    ]),
    "interurbanos": ("gtfs_interurbanos.zip", [
        "https://www.arcgis.com/sharing/rest/content/items/885399f83408473c8d815e40c5e702b7/data",
        "http://crtm.maps.arcgis.com/sharing/rest/content/items/885399f83408473c8d815e40c5e702b7/data",
    ]),
    "ligero": ("gtfs_metro_ligero.zip", [
        "https://crtm.maps.arcgis.com/sharing/rest/content/items/aaed26cc0ff64b0c947ac0bc3e033196/data",
        "https://www.arcgis.com/sharing/rest/content/items/aaed26cc0ff64b0c947ac0bc3e033196/data",
    ]),
    "cercanias": ("gtfs_cercanias.zip", [
        "https://www.arcgis.com/sharing/rest/content/items/1a25440bf66f499bae2657ec7fb40144/data",
        "https://crtm.maps.arcgis.com/sharing/rest/content/items/1a25440bf66f499bae2657ec7fb40144/data",
    ]),
}
# Pagina de la region Madrid (fuente de verdad de las URLs de ficheros)
OSM_PAGINA = "https://download.geofabrik.de/europe/spain/madrid.html"
OSM_MIN_MB = 50

def _sesion():
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "mini-ies-madrid/1.0"
    return s

def descargar(url, destino):
    with _sesion().get(url, stream=True, timeout=600) as r:
        if r.history:
            print(f"  AVISO: redireccion -> {r.url}")
        r.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)

def validar_gtfs(ruta):
    try:
        with zipfile.ZipFile(ruta) as z:
            nombres = set(n.split("/", 1)[0] for n in z.namelist())
            faltan = [f for f in OBLIGATORIOS if f not in nombres]
            if faltan:
                print(f"  INVALIDO: faltan {faltan}. Raiz: {sorted(nombres)[:10]}")
                return False
            return True
    except zipfile.BadZipFile:
        print("  INVALIDO: no es un zip (probablemente una pagina de error)")
        return False

def aplanar_gtfs(ruta):
    with zipfile.ZipFile(ruta) as z:
        raiz = set(n.split("/", 1)[0] for n in z.namelist() if not n.endswith("/"))
        if len(raiz) != 1:
            return False
        carpeta = raiz.pop() + "/"
        dentro = [n for n in z.namelist() if n.startswith(carpeta) and not n.endswith("/")]
        if not any(n[len(carpeta):] in OBLIGATORIOS for n in dentro):
            return False
        tmp = ruta + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for n in dentro:
                out.writestr(n[len(carpeta):], z.read(n))
    os.replace(tmp, ruta)
    print(f"  Aplanado: contenido de '{carpeta}' movido a la raiz")
    return True

def rango_validez_gtfs(ruta):
    fechas = set()
    with zipfile.ZipFile(ruta) as z:
        for fichero in ("calendar.txt", "calendar_dates.txt"):
            if fichero in z.namelist():
                with z.open(fichero) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                        for k in ("start_date", "end_date", "date"):
                            if row.get(k):
                                fechas.add(row[k])
    if not fechas:
        return None, None
    return min(fechas), max(fechas)

def bajar_feed(clave):
    destino, urls = FEEDS[clave]
    for url in urls:
        print(f"Descargando {destino} ...")
        try:
            descargar(url, destino)
        except Exception as e:
            print(f"  ERROR de descarga: {e}")
            continue
        if not validar_gtfs(destino):
            aplanar_gtfs(destino)
        if validar_gtfs(destino):
            ini, fin = rango_validez_gtfs(destino)
            if ini:
                print(f"  OK. Validez: {ini[:4]}-{ini[4:6]}-{ini[6:]} a {fin[:4]}-{fin[4:6]}-{fin[6:]}")
            return True
        print("  Probando URL de respaldo ...")
    return False

def url_osm_fechada():
    """Raspa la pagina de la region y devuelve la URL del snapshot fechado mas
    reciente (p.ej. .../madrid-261115.osm.pbf). None si no puede."""
    r = _sesion().get(OSM_PAGINA, timeout=60)
    r.raise_for_status()
    fechas = re.findall(r"madrid-(\d{6})\.osm\.pbf", r.text)
    if not fechas:
        return None
    fecha = max(fechas)
    base = OSM_PAGINA.rsplit("/", 1)[0]
    return f"{base}/madrid-{fecha}.osm.pbf"

def bajar_osm():
    destino = "madrid-latest.osm.pbf"
    candidatos = [f"{OSM_PAGINA.rsplit('/', 1)[0]}/madrid-latest.osm.pbf"]
    for url in candidatos:
        print(f"Descargando OSM desde {url} ...")
        try:
            descargar(url, destino)
        except Exception as e:
            print(f"  ERROR de descarga: {e}")
        else:
            if os.path.getsize(destino) / 1e6 >= OSM_MIN_MB:
                print(f"  OK: {os.path.getsize(destino)/1e6:.0f} MB")
                return True
            print("  INVALIDO: pesa menos de lo esperado")
    # fallback: snapshot fechado mas reciente
    print("Buscando el snapshot fechado mas reciente en la pagina de la region...")
    url = url_osm_fechada()
    if url:
        print(f"Descargando OSM desde {url} ...")
        try:
            descargar(url, destino)
        except Exception as e:
            print(f"  ERROR de descarga: {e}")
        else:
            if os.path.getsize(destino) / 1e6 >= OSM_MIN_MB:
                print(f"  OK: {os.path.getsize(destino)/1e6:.0f} MB (snapshot fechado)")
                return True
    raise SystemExit("No pude descargar el OSM de Geofabrik. Revisa manualmente "
                     + OSM_PAGINA)

if __name__ == "__main__":
    solo = sys.argv[1] if len(sys.argv) > 1 else None
    todo_ok = True
    for clave in FEEDS:
        if solo in (None, clave):
            todo_ok &= bajar_feed(clave)
    if solo in (None, "osm"):
        bajar_osm()
    if not todo_ok:
        sys.exit("FALLO: algun GTFS no se pudo validar. NO lances el build hasta arreglarlo.")
    print("\nTodo validado. Copia los 5 .zip y el .osm.pbf a otp/data/ y reconstruye.")
