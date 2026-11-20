"""
Paso 2a (v7): descargar, VALIDAR y APLANAR los 5 GTFS + el callejero OSM.
v7: Geofabrik reorganizo rutas (blog 19.11.2026): los .pbf de region pasan de
/europe/spain/madrid-latest.osm.pbf a /europe/spain/madrid/madrid-latest.osm.pbf
y las redirecciones de URLs viejas estan rotas temporalmente. Estrategia:
  1. URL nueva (canonica actual)
  2. URL vieja (por si vuelven a arreglar el redirect)
  3. Indice JSON oficial de Geofabrik (urls.pbf autoritativo por region)
  4. Snapshot fechado mas reciente, raspado de la pagina de la region

Uso:
    python descargar_transportes.py                # todo
    python descargar_transportes.py osm             # solo el OSM
"""
import re
import sys
import io
import csv
import os
import json
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
OSM_NUEVA = "https://download.geofabrik.de/europe/spain/madrid/madrid-latest.osm.pbf"
OSM_VIEJA = "https://download.geofabrik.de/europe/spain/madrid-latest.osm.pbf"
OSM_INDICE = "https://download.geofabrik.de/index-v1-nogeom.json"
OSM_PAGINAS = [
    "https://download.geofabrik.de/europe/spain/madrid/",
    "https://download.geofabrik.de/europe/spain/madrid.html",
]
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

def _pesa_ok(destino):
    mb = os.path.getsize(destino) / 1e6
    if mb < OSM_MIN_MB:
        print(f"  INVALIDO: pesa {mb:.1f} MB (se esperaban ~80)")
        return False
    print(f"  OK: {mb:.0f} MB")
    return True

def url_osm_desde_indice():
    """Indice JSON oficial de Geofabrik: urls.pbf autoritativo para cada region.
    Busca la region de Madrid dentro de Espana (id 'esmd' o nombre)."""
    try:
        r = _sesion().get(OSM_INDICE, timeout=120)
        r.raise_for_status()
        datos = json.loads(r.text)
        for f in datos.get("features", []):
            p = f.get("properties", {})
            urls = p.get("urls", {}) or {}
            es_madrid = (p.get("id") == "esmd"
                         or (p.get("parent") == "es" and "Madrid" in (p.get("name") or "")))
            if es_madrid and urls.get("pbf"):
                return urls["pbf"]
    except Exception as e:
        print(f"  (indice JSON no disponible: {e})")
    return None

def url_osm_fechada():
    """Raspa la pagina de la region y devuelve el snapshot fechado mas reciente."""
    for pagina in OSM_PAGINAS:
        try:
            r = _sesion().get(pagina, timeout=60)
            r.raise_for_status()
            fechas = re.findall(r"madrid-(\d{6})\.osm\.pbf", r.text)
            if fechas:
                base = pagina if pagina.endswith("/") else pagina.rsplit("/", 1)[0]
                return f"{base.rstrip('/')}/madrid-{max(fechas)}.osm.pbf"
        except Exception:
            continue
    return None

def bajar_osm():
    destino = "madrid-latest.osm.pbf"
    candidatos = [OSM_NUEVA, OSM_VIEJA]
    candidatos.append(url_osm_desde_indice())
    candidatos.append(url_osm_fechada())
    for url in [u for u in candidatos if u]:
        print(f"Descargando OSM desde {url} ...")
        try:
            descargar(url, destino)
        except Exception as e:
            print(f"  ERROR de descarga: {e}")
            continue
        if _pesa_ok(destino):
            return True
    raise SystemExit("No pude descargar el OSM de Geofabrik. Consulta "
                     "https://blog.geofabrik.de/ (anuncios de mantenimiento) y "
                     "https://download.geofabrik.de/europe/spain.html")

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
