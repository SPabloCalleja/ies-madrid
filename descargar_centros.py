"""
Paso 1 (v3): catalogo de centros candidatos: IES (cualquier tipo), CEPA y
CEIPSO (en el catalogo de Madrid, "CP INF-PRI-SEC"). SUSTITUYE a
descargar_ies_publicos.py y a la v2 de este script.

Fuente: dataset oficial "Centros educativos de la Comunidad de Madrid"
(datos.comunidad.madrid, CC-BY 4.0). Filtra:
  - SITUACION = ALTA
  - titularidad publica (configurable en TITULARIDADES)
  - TIPO_ABRV en TIPOS_CENTRO: IES, IESO, CEPA, CP INF-PRI-SEC (CEIPSO)

La candidacidad es por TIPO de centro, no por ensenanzas; las fichas
(descargar_ensenanzas.py) solo anaden informacion (ESO/Bach/FP, turnos,
bilingue) para filtros y para el Excel.

Coordenadas: el catalogo trae UTM 30N; la cartografia oficial de la Comunidad
de Madrid es ED50 (EPSG:23030), pero el script se AUTO-CALIBRA contra el CSV
anterior si existe (elige el datum con menor desviacion mediana).

Requisitos: uv add requests pandas pyproj
Uso: uv run descargar_centros.py
"""
import io
import requests
import numpy as np
import pandas as pd

URL = ("https://datos.comunidad.madrid/catalogo/dataset/"
       "c750856d-3166-4dac-8e80-d1b824c968b5/resource/"
       "28d60557-1d73-4281-ab08-6cfd3b2f5f83/download/centros_educativos.csv")
SALIDA = "ies_publicos_madrid.csv"      # mismo nombre: el resto del pipeline no cambia
TITULARIDADES = ["PÚBLICO"]
TIPOS_CENTRO = {"IES", "IESO", "CEPA", "CP INF-PRI-SEC", "CEIPSO"}   # candidatos

def descargar():
    ultimo_error = None
    for intento in range(3):
        try:
            r = requests.get(URL, timeout=120,
                             headers={"User-Agent": "mini-proyecto-ies-madrid/1.0"})
            r.raise_for_status()
            return r.content
        except Exception as e:
            ultimo_error = e
            print(f"  descarga fallo (intento {intento + 1}/3): {e}")
    raise SystemExit(f"No pude descargar el catalogo: {ultimo_error}")

def convertir_latlon(serie_x, serie_y, epsg):
    from pyproj import Transformer
    t = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True)
    lon, lat = t.transform(serie_x.values, serie_y.values)
    return pd.Series(lat), pd.Series(lon)

def _distancia_m(lat1, lon1, lat2, lon2):
    dlat = (np.asarray(lat2) - np.asarray(lat1)) * 111320
    dlon = (np.asarray(lon2) - np.asarray(lon1)) * 111320 * np.cos(np.radians((np.asarray(lat1) + np.asarray(lat2)) / 2))
    return np.hypot(dlat, dlon)

def elegir_epsg(con_coords):
    """ED50 (EPSG:23030) es el estandar de la cartografia de la Comunidad, pero
    si hay un CSV anterior con coordenadas ya validadas, se elige el datum con
    menor desviacion mediana respecto a el."""
    candidatos = {"EPSG:23030": "ED50", "EPSG:32630": "WGS84"}
    try:
        viejo = pd.read_csv(SALIDA, dtype={"CODIGO": str})
    except FileNotFoundError:
        print("Sin CSV previo: uso ED50 (EPSG:23030), estandar de la cartografia de la Comunidad")
        return "EPSG:23030"
    if "lat" not in viejo.columns or viejo["lat"].isna().all():
        return "EPSG:23030"
    ref = viejo.dropna(subset=["lat"]).set_index("CODIGO")
    comunes = con_coords[con_coords["CODIGO"].isin(ref.index)]
    if len(comunes) < 3:
        return "EPSG:23030"
    mejor, mejor_med = "EPSG:23030", None
    for epsg, nombre in candidatos.items():
        lat, lon = convertir_latlon(comunes["UTM_X"], comunes["UTM_Y"], epsg)
        d = _distancia_m(lat.values, lon.values,
                         comunes["CODIGO"].map(ref["lat"]).values,
                         comunes["CODIGO"].map(ref["lon"]).values)
        mediana = float(np.median(d))
        print(f"  {nombre} ({epsg}): desviacion mediana {mediana:.0f} m frente al CSV previo ({len(comunes)} centros)")
        if mejor_med is None or mediana < mejor_med:
            mejor, mejor_med = epsg, mediana
    print(f"  -> uso {candidatos[mejor]} ({mejor})")
    return mejor

def procesar(contenido_csv):
    df = pd.read_csv(io.BytesIO(contenido_csv), sep=";", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    print(f"Catalogo completo: {len(df)} centros")

    activos = df[df["SITUACIÓN"].str.upper() == "ALTA"]
    publicos = activos[activos["TITULARIDAD"].str.upper().isin([t.upper() for t in TITULARIDADES])]
    candidatos = publicos[publicos["TIPO_ABRV"].str.upper().str.strip().isin(TIPOS_CENTRO)]
    candidatos = candidatos.drop_duplicates(subset=["CODIGO"]).copy()
    print(f"Candidatos publicos en activo (IES/IESO/CEPA/CP INF-PRI-SEC): {len(candidatos)} centros")

    for col in ("UTM_X", "UTM_Y"):
        candidatos[col] = pd.to_numeric(candidatos[col], errors="coerce")
    con_coords = candidatos[(candidatos["UTM_X"] > 0) & (candidatos["UTM_Y"] > 0)].copy()
    sin_coords = len(candidatos) - len(con_coords)
    if sin_coords:
        print(f"  {sin_coords} centros sin coordenadas UTM (se omiten)")

    epsg = elegir_epsg(con_coords)
    lat, lon = convertir_latlon(con_coords["UTM_X"], con_coords["UTM_Y"], epsg)

    telefono = (con_coords["TELEFONO"].fillna(con_coords["TELEFONO2"])
                .fillna(con_coords["TELEFONO3"]).fillna(con_coords["TELEFONO4"]))
    telefono = telefono.where(~telefono.isin(["0", ""]), "")
    salida = pd.DataFrame({
        "CODIGO": con_coords["CODIGO"].str.strip(),
        "CENTRO": con_coords["CENTRO"].str.strip(),
        "TIPO_ABRV": con_coords["TIPO_ABRV"].str.strip(),
        "TIPO_EXT": con_coords["TIPO_EXT"].str.strip(),
        "TITULARIDAD": con_coords["TITULARIDAD"].str.strip(),
        "MUNICIPIO": con_coords["MUNICIPIO"].str.strip(),
        "CALLE": con_coords["DOMICILIO"].str.strip(),
        "NUMERO": con_coords["NMVIAL"].str.strip(),
        "CP": con_coords["CDPOSTAL"].str.strip(),
        "AREA_TERRITORIAL": con_coords["DAT"].str.strip(),
        "TELEFONO": telefono.str.strip(),
        "WEB": con_coords["WEB"].str.strip(),
        "E_MAIL": con_coords["E_MAIL"].str.strip(),
        "lat": lat.values,
        "lon": lon.values,
    }).reset_index(drop=True)
    salida.to_csv(SALIDA, index=False, encoding="utf-8")

    print(f"\nGuardado {SALIDA}: {len(salida)} centros")
    print("\nPor tipo de centro:")
    print(salida["TIPO_ABRV"].value_counts().to_string())
    return salida

def main():
    procesar(descargar())

if __name__ == "__main__":
    main()
