"""
Descarga versiones frescas de los GTFS caducados y los sustituye in situ
dejando una copia .bak. (v5)

Contexto: el dataset de METRO del portal del CRTM lleva sin publicarse desde
mayo de 2025 (version 20250527, caduca 2026-05-27). El canal oficial que
queda por probar es el Punto de Acceso Nacional (NAP) del Ministerio
(nap.transportes.gob.es), que exige registro gratuito y ApiKey. Este script
sabe hablar con el NAP: buscar por operador, listar ficheros y descargarlos.

Fuentes automaticas, en orden:
  1) URLs DIRECTAS de los items de ArcGIS del CRTM (algunos vivos: metro
     ligero, EMT...; el de metro esta huerfano) y el GTFS semanal de EMT.
  2) Catalogo DCAT del portal del CRTM.
  3) NAP del Ministerio (necesita --apikey).
  4) A mano: --fichero X.zip o --url, con validacion y .bak.

Antes de reemplazar nada valida que el zip es GTFS, que cubre la fecha de
calculo (2026-11-24) y que la agencia casa con la del feed viejo.

Uso:
  uv run descargar_gtfs.py                            (solo los caducados)
  uv run descargar_gtfs.py --todo
  uv run descargar_gtfs.py --apikey K --operador "Metro de Madrid"
  uv run descargar_gtfs.py --apikey K --nap-listar [texto]
  uv run descargar_gtfs.py --apikey K --nap ID [--sobre viejo.zip]
  uv run descargar_gtfs.py --fichero X [--sobre Y]  ·  --url X [--sobre Y]
  uv run descargar_gtfs.py --debug

Despues: reconstruir el grafo de OTP y re-ejecutar matriz_tiempos_otp.py.
"""
import glob
import io
import json
import os
import shutil
import sys
import urllib.parse
import zipfile

import requests
import pandas as pd

FECHA_OBJETIVO = "20261124"
CABECERA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
NAP = "https://nap.transportes.gob.es"
TIEMPO_ESPERA = 90
DEPURAR = "--debug" in sys.argv

_ITEM = "https://crtm.maps.arcgis.com/sharing/rest/content/items/%s/data"
_ITEM_ALT = "https://www.arcgis.com/sharing/rest/content/items/%s/data"

CANDIDATOS = {
    "metro": [
        _ITEM % "5c7f2951962540d69ffe8f640d94c246",      # huerfano desde 2025-05
        _ITEM_ALT % "5c7f2951962540d69ffe8f640d94c246",
    ],
    "metro ligero": [
        _ITEM % "aaed26cc0ff64b0c947ac0bc3e03196",       # vivo (mdb-2802)
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
        _ITEM % "1a25440bf66f499bae2657ec7fb40144",
        _ITEM_ALT % "1a25440bf66f499bae2657ec7fb40144",
    ],
}

DCAT = [
    "https://datos.crtm.es/api/feed/dcat/1.0.json",
    "https://data-crtm.opendata.arcgis.com/api/feed/dcat/1.0.json",
]

# (tipo, pistas en agency_name del zip viejo, pistas en el nombre del fichero)
TIPOS = [
    ("metro ligero", ["ligero", "tranv"], ["ligero", "tranv"]),
    ("metro", ["metro de madrid"], ["metro"]),
    ("interurbanos", ["alsa", "interurban", "larrea", "aumo"], ["interurban", "carretera"]),
    ("urbanos EMT", ["emt", "empresa municipal", "urbanos de madrid"], ["emt", "urban"]),
    ("cercanias", ["renfe", "cercan"], ["cercan", "renfe"]),
]

PAGINAS_MANUALES = {
    "metro": "https://datos.crtm.es/datasets/5c7f2951962540d69ffe8f640d94c246 (huerfano desde 2025-05; prueba el NAP)",
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


def feeds_locales():
    salida = []
    for z in sorted(glob.glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as f:
                if "agency.txt" not in f.namelist():
                    continue
                ag, inicio, fin = rango_feed(f)
        except zipfile.BadZipFile:
            continue
        salida.append((z, ag, inicio, fin))
    return salida


def bajar(url, binario=True, cabeceras=None):
    r = requests.get(url, headers=cabeceras or CABECERA, timeout=TIEMPO_ESPERA,
                     allow_redirects=True)
    if DEPURAR:
        print(f"    [http] {r.status_code} {url[:90]}")
    r.raise_for_status()
    return r.content if binario else r.text


def validar_zip(datos, agencia_vieja=None):
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


# ------------------------- NAP (Ministerio) -------------------------

def _cab_nap(clave):
    return {"ApiKey": clave, "accept": "application/json"}

def nap_get(ruta, clave):
    r = requests.get(f"{NAP}{ruta}", headers=_cab_nap(clave), timeout=TIEMPO_ESPERA)
    if DEPURAR:
        print(f"    [http] {r.status_code} {ruta[:80]}")
    r.raise_for_status()
    return r.json()

def _iterar_dicts(nodo):
    if isinstance(nodo, dict):
        yield nodo
        for v in nodo.values():
            yield from _iterar_dicts(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from _iterar_dicts(v)

def _valores_texto(nodo):
    if isinstance(nodo, str):
        return [nodo]
    if isinstance(nodo, dict):
        return [s for v in nodo.values() for s in _valores_texto(v)]
    if isinstance(nodo, list):
        return [s for v in nodo for s in _valores_texto(v)]
    return []

def _id_de(d):
    return next((d[k] for k in ("id", "Id", "ID", "ficheroId", "ficheroID")
                 if k in d and d[k] is not None), None)

def nap_listar(clave, filtro=None):
    """Nodos con id del GetList (conjuntos y sus ficheros anidados), filtrados."""
    datos = nap_get("/api/Fichero/GetList", clave)
    if DEPURAR:
        print("    [nap] cabecera de la respuesta:", str(datos)[:400])
    filas = []
    for d in _iterar_dicts(datos):
        ident = _id_de(d)
        if ident is None:
            continue
        resumen = " · ".join(v for v in _valores_texto(d) if len(v) > 2)[:110]
        if filtro and filtro.lower() not in resumen.lower():
            continue
        filas.append((ident, resumen))
    return filas

def nap_operador(clave, nombre):
    """Conjuntos de datos de un operador (p.ej. 'Metro de Madrid')."""
    ruta = "/api/Operador/GetByName/" + urllib.parse.quote(nombre)
    try:
        datos = nap_get(ruta, clave)
    except (requests.RequestException, ValueError) as e:
        print(f"No pude consultar el operador: {e}")
        return
    filas = []
    for d in _iterar_dicts(datos):
        ident = _id_de(d)
        if ident is None:
            continue
        resumen = " · ".join(v for v in _valores_texto(d) if len(v) > 2)[:110]
        if any(p in resumen.lower() for p in ("gtfs", "horario", "frecuencia", "calendario")):
            filas.append((ident, resumen))
    if filas:
        print(f"Nodos relacionados con horarios/GTFS del operador '{nombre}':")
        for ident, resumen in filas[:60]:
            print(f"  {str(ident):8} {resumen}")
    else:
        print("Ningun nodo de horarios/GTFS; volcado crudo (recortado):")
        print(json.dumps(datos, ensure_ascii=False, indent=1)[:2500])
        return
    print("\nPara descargar un fichero: --apikey K --nap ID (prueba tambien")
    print("--nap-listar 'nombre del conjunto' para ver todos sus ficheros)")

def nap_descargar(clave, fichero_id):
    """downloadLink/{id} devuelve un enlace firmado; si no, prueba download/{id}."""
    for url in (f"{NAP}/api/Fichero/downloadLink/{fichero_id}",
                f"{NAP}/api/Fichero/download/{fichero_id}"):
        try:
            r = requests.get(url, headers=_cab_nap(clave), timeout=TIEMPO_ESPERA,
                             allow_redirects=True)
        except requests.RequestException:
            continue
        if DEPURAR:
            print(f"    [http] {r.status_code} {url[:90]}")
        if r.status_code != 200:
            continue
        enlace = None
        try:
            j = r.json()
            if isinstance(j, str) and j.startswith("http"):
                enlace = j
            elif isinstance(j, dict):
                enlace = next((v for v in j.values()
                               if isinstance(v, str) and v.startswith("http")), None)
        except ValueError:
            if r.text.strip().startswith("http"):
                enlace = r.text.strip()
        if enlace:
            return bajar(enlace)
        ctype = r.headers.get("content-type", "")
        if "json" not in ctype and len(r.content) > 1000:
            return r.content
    return None


def instalar_manual(datos, ruta_origen=None, forzar_sobre=None):
    valido = validar_zip(datos)
    if valido is None:
        print("El zip no valida: no parece GTFS, no cubre el "
              f"{FECHA_OBJETIVO} o no se puede leer")
        return False
    ag, fin = valido
    print(f"Feed de: {ag} · servicio hasta {fin}")
    locales = feeds_locales()
    if forzar_sobre is not None:
        objetivo = forzar_sobre
    else:
        origen = os.path.basename(ruta_origen) if ruta_origen else None
        parecidos = [z for z, a, _, _ in locales
                     if a and a.split()[0].lower() in ag.lower() and z != origen]
        if len(parecidos) == 1:
            objetivo = parecidos[0]
            print(f"Sustituira a tu feed local: {objetivo}")
        elif len(parecidos) == 0:
            print("No encuentro un feed local de esa agencia; dime donde "
                  "instalarlo: --sobre NOMBRE.zip")
            print("Feeds locales:", ", ".join(z for z, a, _, _ in locales) or "ninguno")
            return False
        else:
            print("Hay varios feeds que podrian ser el mismo; concretame: "
                  "--sobre NOMBRE.zip")
            print("Candidatos:", ", ".join(parecidos))
            return False
    instalar(objetivo, datos, valido)
    if (ruta_origen and os.path.exists(ruta_origen)
            and os.path.abspath(ruta_origen) != os.path.abspath(objetivo)):
        os.remove(ruta_origen)
        print(f"  retirado {ruta_origen} (ya esta instalado dentro de {objetivo})")
    print("Recuerda reconstruir el grafo de OTP y re-ejecutar la matriz.")
    return True


def _valor(argv, opcion):
    return argv[argv.index(opcion) + 1] if opcion in argv else None


def main():
    argv = sys.argv[1:]
    if "--operador" in argv:
        clave = _valor(argv, "--apikey")
        nombre = _valor(argv, "--operador")
        if not (clave and nombre):
            print('uso: --apikey CLAVE --operador "Metro de Madrid"')
            return
        nap_operador(clave, nombre)
        return
    if "--nap-listar" in argv:
        clave = _valor(argv, "--apikey")
        if not clave:
            print("hace falta --apikey TUCLAVE (registro gratuito en nap.transportes.gob.es)")
            return
        i = argv.index("--nap-listar")
        filtro = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        try:
            filas = nap_listar(clave, filtro)
        except (requests.RequestException, ValueError) as e:
            print(f"No pude listar el NAP: {e}")
            return
        print(f"{len(filas)} nodos del NAP"
              + (f" que contienen '{filtro}'" if filtro else "") + ":")
        for ident, resumen in filas[:80]:
            print(f"  {str(ident):8} {resumen}")
        if not filas:
            print("(si sale 0, prueba sin filtro o con --debug para ver la respuesta)")
        print("\nPara descargar un fichero: uv run descargar_gtfs.py --apikey K --nap ID")
        return
    if "--nap" in argv:
        clave = _valor(argv, "--apikey")
        ident = _valor(argv, "--nap")
        sobre = _valor(argv, "--sobre")
        if not (clave and ident):
            print("uso: --apikey CLAVE --nap ID [--sobre viejo.zip]")
            return
        print(f"Descargando del NAP el fichero {ident} ...")
        datos = nap_descargar(clave, ident)
        if datos is None:
            print("No pude descargarlo del NAP (revisa la ApiKey y el ID; --debug ayuda)")
            return
        instalar_manual(datos, ruta_origen=None, forzar_sobre=sobre)
        return
    if "--fichero" in argv:
        ruta = _valor(argv, "--fichero")
        sobre = _valor(argv, "--sobre")
        try:
            with open(ruta, "rb") as f:
                datos = f.read()
        except OSError as e:
            print(f"No puedo leer {ruta}: {e}")
            return
        instalar_manual(datos, ruta_origen=ruta, forzar_sobre=sobre)
        return
    if "--url" in argv:
        url = _valor(argv, "--url")
        sobre = _valor(argv, "--sobre")
        print(f"Descargando {url} ...")
        try:
            datos = bajar(url)
        except requests.RequestException as e:
            print(f"ERROR descargando: {e}")
            return
        instalar_manual(datos, ruta_origen=None, forzar_sobre=sobre)
        return

    todo = "--todo" in argv
    locales = feeds_locales()
    if not locales:
        print("No hay GTFS .zip en esta carpeta: ejecutame donde los tengas")
        return

    print(f"Fecha de calculo del proyecto: {FECHA_OBJETIVO[:4]}-{FECHA_OBJETIVO[4:6]}-{FECHA_OBJETIVO[6:]}")
    objetivo = []
    for z, ag, _, fin in locales:
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
            print("  o registra una ApiKey gratuita en nap.transportes.gob.es y usa:")
            print('  uv run descargar_gtfs.py --apikey K --operador "Metro de Madrid"')
            fallos += 1
            continue
        instalar(z, datos, valido)
        ok += 1

    print(f"\nActualizados: {ok} · fallos: {fallos}")
    if ok:
        print("Siguientes pasos: reconstruir el grafo de OTP (docker build +")
        print("reinicio del contenedor) y re-ejecutar matriz_tiempos_otp.py.")


if __name__ == "__main__":
    main()
