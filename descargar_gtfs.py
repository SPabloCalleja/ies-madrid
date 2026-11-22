"""
Descarga versiones frescas de los GTFS caducados, del portal de datos abiertos
del CRTM (datos.crtm.es), y los sustituye in situ dejando una copia .bak.

Que hace:
  1) Escanea los .zip de la carpeta actual (los que montas al construir OTP)
     y detecta los que NO cubren la fecha de calculo del proyecto (2026-11-24).
  2) Consulta el catalogo DCAT del portal del CRTM y localiza los datasets
     "GTFS Red de ..." (Metro, Metro Ligero, EMT, Interurbanos, Cercanias).
  3) Descarga el zip nuevo, valida que es GTFS, que cubre la fecha y que la
     agencia casa con la del viejo, y reemplaza el fichero con el MISMO nombre
     (asi el build de OTP no cambia).
Despues toca reconstruir el grafo de OTP (docker) y re-ejecutar la matriz.

Uso:  uv run descargar_gtfs.py           (solo los caducados)
      uv run descargar_gtfs.py --todo    (refresca los que haya, caduquen o no)
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
DCAT = [
    "https://datos.crtm.es/api/feed/dcat/1.0.json",
    "https://data-crtm.opendata.arcgis.com/api/feed/dcat/1.0.json",
]
CABECERA = {"User-Agent": "mini-proyecto-ies-madrid/1.0"}
TIEMPO_ESPERA = 60

# (tipo, pistas en agency_name del zip viejo, pistas en el nombre del fichero)
# ojo al orden: interurbanos antes que urbanos ("interurbanos" contiene "urban")
TIPOS = [
    ("metro ligero", ["ligero", "tranv"], ["ligero", "tranv"]),
    ("metro", ["metro de madrid"], ["metro"]),
    ("interurbanos", ["alsa", "interurban", "larrea", "aumo"], ["interurban", "carretera"]),
    ("urbanos EMT", ["emt", "empresa municipal", "urbanos de madrid"], ["emt", "urban"]),
    ("cercanias", ["renfe", "cercan"], ["cercan", "renfe"]),
]

# si el DCAT no responde, paginas para descargar a mano
PAGINAS_MANUALES = {
    "metro": "https://datos.crtm.es/datasets/5c7f2951962540d69ffe8f640d94c246",
    "metro ligero": "https://datos.crtm.es/datasets/aaed26cc0ff64b0c947ac0bc3e033196",
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


def _urls_zip(nodo):
    if isinstance(nodo, str):
        return [nodo] if nodo.lower().startswith("http") and nodo.lower().endswith(".zip") else []
    if isinstance(nodo, dict):
        return [u for v in nodo.values() for u in _urls_zip(v)]
    if isinstance(nodo, list):
        return [u for v in nodo for u in _urls_zip(v)]
    return []


def _buscar_datasets(nodo, salida):
    """Recorre el DCAT buscando nodos-dataset con 'GTFS' en el titulo."""
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


def pedir_dcat():
    for url in DCAT:
        try:
            r = requests.get(url, headers=CABECERA, timeout=TIEMPO_ESPERA)
            if r.status_code == 200:
                return json.loads(r.text)
        except (requests.RequestException, ValueError):
            continue
    return None


def url_para(tipo, datasets):
    """Elige el dataset cuyo titulo casa con el tipo pedido."""
    def titulo_casa(titulo):
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
        if titulo_casa(titulo) and urls:
            return titulo, urls[0]
    return None, None


def descargar_zip(url):
    r = requests.get(url, headers=CABECERA, timeout=TIEMPO_ESPERA)
    r.raise_for_status()
    return r.content


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


def main():
    todo = "--todo" in sys.argv
    zips = sorted(p for p in glob.glob("*.zip"))
    locales = []
    for z in zips:
        try:
            with zipfile.ZipFile(z) as f:
                if "agency.txt" not in f.namelist():
                    continue
                ag, inicio, fin = rango_feed(f)
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
            print(f"    no se de que red es este feed: revisalo a mano")

    if not objetivo:
        print("\nNada que descargar" + (" (--todo desactivado)" if not todo else ""))
        return

    print("\nConsultando el catalogo del CRTM ...")
    dcat = pedir_dcat()
    datasets = []
    if dcat is not None:
        _buscar_datasets(dcat, datasets)
    if not datasets:
        print("No pude leer el catalogo DCAT. Descarga a mano y reemplaza:")
        for _, _, tipo in objetivo:
            print(f"  {tipo:14} {PAGINAS_MANUALES.get(tipo, 'datos.crtm.es')}")
        return
    print(f"Encontrados {len(datasets)} datasets GTFS en el catalogo")

    ok, fallos = 0, 0
    for z, ag, tipo in objetivo:
        titulo, url = url_para(tipo, datasets)
        if url is None:
            print(f"\n[{tipo}] no encuentro su dataset en el catalogo: "
                  f"{PAGINAS_MANUALES.get(tipo, '')}")
            fallos += 1
            continue
        print(f"\n[{tipo}] {titulo}")
        print(f"  descargando {url} ...")
        try:
            datos = descargar_zip(url)
            valido = validar_zip(datos, ag)
        except requests.RequestException as e:
            print(f"  ERROR descargando: {e}")
            fallos += 1
            continue
        if valido is None:
            print("  el zip descargado no valida (no GTFS, no cubre la fecha "
                  "o la agencia no casa): baja la version de a mano")
            fallos += 1
            continue
        ag_nueva, fin_nuevo = valido
        shutil.copy2(z, z + ".bak")
        with open(z, "wb") as f:
            f.write(datos)
        print(f"  {z} actualizado ({ag_nueva}, servicio hasta {fin_nuevo}); "
              f"copia de seguridad en {z}.bak")
        ok += 1

    print(f"\nActualizados: {ok} · fallos: {fallos}")
    if ok:
        print("Siguientes pasos: reconstruir el grafo de OTP (docker build +")
        print("reinicio del contenedor) y re-ejecutar matriz_tiempos_otp.py.")


if __name__ == "__main__":
    main()
