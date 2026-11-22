"""
Descarga versiones frescas de los GTFS caducados y los sustituye in situ
dejando una copia .bak. (v2)

Fuentes, en este orden:
  1) URLs DIRECTAS de los items de ArcGIS (crtm.maps.arcgis.com), que publica
     Mobility Database como URLs productoras de cada feed, y el GTFS semanal
     oficial de EMT. No hace falta ningun catalogo.
  2) Catalogo DCAT del portal del CRTM (por si las URLs directas cambian).
  3) Modo manual: uv run descargar_gtfs.py --url "https://...zip"

Antes de reemplazar nada valida que el zip es GTFS, que cubre la fecha de
calculo (2026-11-24) y que la agencia casa con la del feed viejo.

Uso:  uv run descargar_gtfs.py                        (solo los caducados)
      uv run descargar_gtfs.py --todo                 (todos los feeds)
      uv run descargar_gtfs.py --url X [--sobre Y.zip] (manual, valida y cambia)
      uv run descargar_gtfs.py --debug                (ver codigos HTTP)

Despues: reconstruir el grafo de OTP y re-ejecutar matriz_tiempos_otp.py.
"""
import glob
import io
import json
import os
import shutil
import sys
import zipfile

import requests
import pandas as pd

FECHA_OBJETIVO = "20261124"
CABECERA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
TIEMPO_ESPERA = 90
DEPURAR = "--debug" in sys.argv

_ITEM = "https://crtm.maps.arcgis.com/sharing/rest/content/items/%s/data"
_ITEM_ALT = "https://www.arcgis.com/sharing/rest/content/items/%s/data"

# URLs candidatas por red, en orden de preferencia
CANDIDATOS = {
    "metro": [
        _ITEM % "5c7f2951962540d69ffe8f640d94c246",      # mdb-794 (productor oficial)
        _ITEM_ALT % "5c7f2951962540d69ffe8f640d94c246",
    ],
    "metro ligero": [
        _ITEM % "aaed26cc0ff64b0c947ac0bc3e03196",       # mdb-792
        _ITEM_ALT % "aaed26cc0ff64b0c947ac0bc3e03196",
    ],
    "urbanos EMT": [
        "http://servicios.emtmadrid.es:8080/GTFS/transitEMT.zip",  # oficial semanal
        _ITEM % "357e63c2904f43aeb5d8a267a64346d8",
        _ITEM_ALT % "357e63c2904f43aeb5d8a267a64346d8",
    ],
    "interurbanos": [
        _ITEM % "885399f83408473c8d815e40c5e702b7",
        _ITEM_ALT % "885399f83408473c8d815e40c5e702b7",
    ],
    "cercanias": [
        _ITEM % "1a25440bf66f499bae2657ec7fb40144",      # dataset cercanias
        _ITEM_ALT % "1a25440bf66f499bae2657ec7fb40144",
    ],
}

DCAT = [
    "https://datos.crtm.es/api/feed/dcat/1.0.json",
    "https://data-crtm.opendata.arcgis.com/api/feed/dcat/1.0.json",
]

# (tipo, pistas en agency_name del zip viejo, pistas en el nombre del fichero)
# ojo al orden: interurbanos antes que urbanos ("interurbanos" contiene "urban")
TIPOS = [
    ("metro ligero", ["ligero", "tranv"], ["ligero", "tranv"]),
    ("metro", ["metro de madrid"], ["metro"]),
    ("interurbanos", ["alsa", "interurban", "larrea", "aumo"], ["interurban", "carretera"]),
    ("urbanos EMT", ["emt", "empresa municipal", "urbanos de madrid"], ["emt", "urban"]),
    ("cercanias", ["renfe", "cercan"], ["cercan", "renfe"]),
]

PAGINAS_MANUALES = {
    "metro": "https://datos.crtm.es/datasets/5c7f2951962540d69ffe8f640d94c246",
    "metro ligero": "https://datos.crtm.es/datasets/aaed26cc0ff64b0c947ac0bc3e03196",
    "urbanos EMT": "https://datos.crtm.es/datasets/357e63c2904f43aeb5d8a267a64346d8",
    "interurbanos": "https://datos.crtm.es/datasets/885399f83408473c8d815e40c5e702b7",
    "cercanias": "buscar 'GTFS Red de Cercanias' en https://datos.crtm.es",
}


def rango_feed(f):
    nombres = set(f.namelist())
    ag = ""
    if "agency.txt" in nombres:
        ag = str(pd.read_csv(io.BytesIO(f.read("agency.txt"))).iloc[0].get("agency_name", ""))
    inicio = fin = None
    if "calendar.txt" in nombres:
        cal = pd.read_csv(io.BytesIO(f.read("calendar.txt")), dtype=str)
        if len(cal):
            inicio = cal["start_date"].min()
            fin = cal["end_date"].max()
    if "calendar_dates.txt" in nombres:
        cd = pd.read_csv(io.BytesIO(f.read("calendar_dates.txt")), dtype=str)
        if len(cd):
            inicio = min(x for x in (inicio, cd["date"].min()) if x)
            fin = max(x for x in (fin, cd["date"].max()) if x)
    return ag, inicio, fin


def clasificar_tipo(agencia, fichero):
    a = (agencia or "").lower()
    n = os.path.basename(fichero).lower()
    for tipo, pistas_ag, pistas_nombre in TIPOS:
        if any(p in a for p in pistas_ag) or any(p in n for p in pistas_nombre):
            return tipo
    return None


def bajar(url, binario=True):
    r = requests.get(url, headers=CABECERA, timeout=TIEMPO_ESPERA, allow_redirects=True)
    if DEPURAR:
        print(f"    [http] {r.status_code} {url[:90]}")
    r.raise_for_status()
    return r.content if binario else r.text


def validar_zip(datos, agencia_vieja):
    """(agencia, fin) del zip nuevo, o None si no es GTFS valido."""
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as f:
            if "agency.txt" not in f.namelist():
                return None
            ag, _, fin = rango_feed(f)
    except zipfile.BadZipFile:
        return None
    if fin is None or fin < FECHA_OBJETIVO:
        return None
    if agencia_vieja and "interurbanos" not in (clasificar_tipo(agencia_vieja, "") or ""):
        v, n = agencia_vieja.lower(), (ag or "").lower()
        if v and n and v.split()[0] not in n:
            return None
    return ag, fin


def instalar(z, datos, valido):
    ag_nueva, fin_nuevo = valido
    shutil.copy2(z, z + ".bak")
    with open(z, "wb") as f:
        f.write(datos)
    print(f"  {z} actualizado ({ag_nueva}, servicio hasta {fin_nuevo}); "
          f"copia en {z}.bak")


def _urls_zip(nodo):
    if isinstance(nodo, str):
        return [nodo] if nodo.lower().startswith("http") and nodo.lower().endswith(".zip") else []
    if isinstance(nodo, dict):
        return [u for v in nodo.values() for u in _urls_zip(v)]
    if isinstance(nodo, list):
        return [u for v in nodo for u in _urls_zip(v)]
    return []


def _buscar_datasets(nodo, salida):
    if isinstance(nodo, dict):
        titulo = None
        for clave, valor in nodo.items():
            if "title" in clave.lower() and isinstance(valor, str):
                titulo = valor
                break
        if titulo and "GTFS" in titulo.upper():
            salida.append((titulo, _urls_zip(nodo)))
            return
        for valor in nodo.values():
            _buscar_datasets(valor, salida)
    elif isinstance(nodo, list):
        for valor in nodo:
            _buscar_datasets(valor, salida)


def url_para(tipo, datasets):
    def casa(titulo):
        t = titulo.upper()
        if "GTFS" not in t:
            return False
        if tipo == "metro":
            return "METRO" in t and "LIGERO" not in t and "TRANV" not in t
        if tipo == "metro ligero":
            return "LIGERO" in t or "TRANV" in t
        if tipo == "urbanos EMT":
            return ("EMT" in t or "URBAN" in t) and "INTERURBAN" not in t
        if tipo == "cercanias":
            return "CERCAN" in t
        if tipo == "interurbanos":
            return "INTERURBAN" in t
        return False
    for titulo, urls in datasets:
        if casa(titulo) and urls:
            return urls[0]
    return None


def descargar_para(tipo, agencia_vieja):
    """Prueba las URLs directas y, si ninguna valida, el DCAT."""
    for url in CANDIDATOS.get(tipo, []):
        print(f"  descargando {url} ...")
        try:
            datos = bajar(url)
        except requests.RequestException as e:
            print(f"    fallo: {e}")
            continue
        valido = validar_zip(datos, agencia_vieja)
        if valido:
            return datos, valido
        print("    no valida (no GTFS, no cubre la fecha o agencia distinta)")
    print("  probando el catalogo DCAT del CRTM ...")
    for url_dcat in DCAT:
        try:
            dcat = bajar(url_dcat, binario=False)
            datasets = []
            _buscar_datasets(json.loads(dcat), datasets)
            url = url_para(tipo, datasets)
            if url:
                datos = bajar(url)
                valido = validar_zip(datos, agencia_vieja)
                if valido:
                    return datos, valido
        except (requests.RequestException, ValueError):
            continue
    return None, None


def main():
    argv = sys.argv[1:]
    if "--url" in argv:
        return modo_manual(argv)

    todo = "--todo" in argv
    zips = sorted(p for p in glob.glob("*.zip"))
    locales = []
    for z in zips:
        try:
            with zipfile.ZipFile(z) as f:
                if "agency.txt" not in f.namelist():
                    continue
                ag, _, fin = rango_feed(f)
        except zipfile.BadZipFile:
            continue
        locales.append((z, ag, fin))
    if not locales:
        print("No hay GTFS .zip en esta carpeta: ejecutame donde los tengas")
        return

    print(f"Fecha de calculo del proyecto: {FECHA_OBJETIVO[:4]}-{FECHA_OBJETIVO[4:6]}-{FECHA_OBJETIVO[6:]}")
    objetivo = []
    for z, ag, fin in locales:
        caducado = (fin is None) or (fin < FECHA_OBJETIVO)
        tipo = clasificar_tipo(ag, z)
        print(f"  {z:40} {ag[:24]:26} fin {fin or '???'}  "
              f"{'CADUCADO' if caducado else 'OK'}")
        if (caducado or todo) and tipo:
            objetivo.append((z, ag, tipo))
        elif caducado and not tipo:
            print("    no se de que red es este feed: revisalo a mano")

    if not objetivo:
        print("\nNada que descargar")
        return

    ok, fallos = 0, 0
    for z, ag, tipo in objetivo:
        print(f"\n[{tipo}] {z}")
        datos, valido = descargar_para(tipo, ag)
        if datos is None:
            print(f"  ninguna fuente valido; a mano: {PAGINAS_MANUALES.get(tipo, 'datos.crtm.es')}")
            fallos += 1
            continue
        instalar(z, datos, valido)
        ok += 1

    print(f"\nActualizados: {ok} · fallos: {fallos}")
    if ok:
        print("Siguientes pasos: reconstruir el grafo de OTP (docker build +")
        print("reinicio del contenedor) y re-ejecutar matriz_tiempos_otp.py.")


def modo_manual(argv):
    i = argv.index("--url")
    if i + 1 >= len(argv):
        print("uso: --url URL [--sobre fichero.zip]")
        return
    url = argv[i + 1]
    zips = sorted(p for p in glob.glob("*.zip"))
    sobre = None
    if "--sobre" in argv:
        j = argv.index("--sobre")
        sobre = argv[j + 1]
    print(f"Descargando {url} ...")
    try:
        datos = bajar(url)
    except requests.RequestException as e:
        print(f"ERROR descargando: {e}")
        return
    ag, _, fin = None, None, None
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as f:
            if "agency.txt" in f.namelist():
                ag, _, fin = rango_feed(f)
    except zipfile.BadZipFile:
        pass
    if ag is None:
        print("Lo descargado no parece un GTFS (sin agency.txt)")
        return
    print(f"Feed de: {ag} · servicio hasta {fin}")
    if fin and fin < FECHA_OBJETIVO:
        print(f"AVISO: no cubre el {FECHA_OBJETIVO} (termina {fin})")
    if sobre is None:
        candidatos = [(z, a) for z, a in
                      [(z, rango_feed(zipfile.ZipFile(z))[0]) for z in zips
                       if zipfile.is_zipfile(z)]
                      if a and a.split()[0].lower() in (ag or "").lower()]
        if len(candidatos) == 1:
            sobre = candidatos[0][0]
            print(f"Coincide con tu feed local: {sobre}")
        else:
            print("Dime en que fichero lo instalo: --sobre NOMBRE.zip")
            print("Feeds locales:", ", ".join(z for z, a in
                  [(z, rango_feed(zipfile.ZipFile(z))[0]) for z in zips
                   if zipfile.is_zipfile(z)] if a))
            return
    instalar(sobre, datos, (ag, fin))
    print("Recuerda reconstruir el grafo de OTP y re-ejecutar la matriz.")


if __name__ == "__main__":
    main()
