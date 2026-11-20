"""
Paso 2a (v3): descargar, VALIDAR y APLANAR los 5 GTFS + el callejero OSM.
- Valida que cada zip tenga los ficheros GTFS obligatorios en la raiz.
- Si los trae anidados en una subcarpeta (p.ej. google_transit_M89/), la aplana.
- URL de respaldo para cada feed.

Uso:
    python descargar_transportes.py                # todo
    python descargar_transportes.py interurbanos   # solo ese feed
"""
import sys
import io
import csv
import os
import zipfile
import requests

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
OSM_URL = "https://download.geofabrik.de/europe/spain/madrid-latest.osm.pbf"
OBLIGATORIOS = ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]

def descargar(url, destino):
    with requests.get(url, stream=True, timeout=600) as r:
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
    """Si los ficheros GTFS estan dentro de una subcarpeta (p.ej. google_transit_M89/),
    reempaqueta el zip dejandolos en la raiz."""
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
        print("Descargando madrid-latest.osm.pbf ...")
        descargar(OSM_URL, "madrid-latest.osm.pbf")
    if not todo_ok:
        sys.exit("FALLO: algun GTFS no se pudo validar. NO lances el build hasta arreglarlo.")
    print("\nTodo validado. Copia los 5 .zip y el .osm.pbf a otp/data/ y reconstruye.")
