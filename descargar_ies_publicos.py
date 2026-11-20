"""
Paso 1 del miniproyecto: descarga y filtra todos los IES públicos de la Comunidad de Madrid.
Fuente: datos.comunidad.madrid - "Centros educativos de la comunidad de Madrid" (CC relevantes).
Requisitos: pip install pandas pyproj requests
"""
import io
import requests
import pandas as pd
from pyproj import Transformer

URL = ("https://datos.comunidad.madrid/catalogo/dataset/c750856d-3166-4dac-8e80-"
       "d1b824c968b5/resource/28d60557-1d73-4281-ab08-6cfd3b2f5f83/download/centros_educativos.csv")

# 1. Descargar y parsear (separador ';', la codificación del fichero es utf-8)
r = requests.get(URL, timeout=60)
r.raise_for_status()
df = pd.read_csv(io.BytesIO(r.content), sep=";", dtype=str, keep_default_na=False)
print(f"Centros totales en el CSV (incluye históricos dados de baja): {len(df)}")

# 2. Inspección: tipos de centro públicos que imparten secundaria
#    (descomenta para revisar; incluye IES, IESO, y otros como CPR PRI-SEC concertados)
# print(df[df.TITULARIDAD == "PÚBLICO"]["TIPO_ABRV"].value_counts())

# 3. Filtrar: institutos públicos en activo
ies = df[
    df["TIPO_ABRV"].isin(["IES", "IESO"])
    & (df["TITULARIDAD"] == "PÚBLICO")
    & (df["SITUACIÓN"] == "ALTA")
].copy()
print(f"IES públicos en situación ALTA: {len(ies)}")

# 4. Coordenadas: UTM 30N ETRS89 (EPSG:25830) -> WGS84 (lat/lon)
ies["UTM_X"] = pd.to_numeric(ies["UTM_X"], errors="coerce")
ies["UTM_Y"] = pd.to_numeric(ies["UTM_Y"], errors="coerce")
sin_coords = ies["UTM_X"].isna() | ies["UTM_Y"].isna()
if sin_coords.any():
    print("Sin coordenadas (habrá que geocodificarlos aparte):")
    print(ies.loc[sin_coords, ["CODIGO", "CENTRO", "MUNICIPIO"]].to_string(index=False))
ies = ies[~sin_coords].copy()

t = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)
ies["lon"], ies["lat"] = t.transform(ies["UTM_X"].values, ies["UTM_Y"].values)

# 5. Columnas útiles para el siguiente paso (matriz r5py + revisión humana)
salida = ies[[
    "CODIGO", "CENTRO", "TIPO_ABRV", "MUNICIPIO", "DISTRITO",
    "CDTPVIA", "DOMICILIO", "NMVIAL", "CDPOSTAL", "DAT",
    "TELEFONO", "WEB", "E_MAIL", "lat", "lon",
]].rename(columns={
    "CDTPVIA": "TIPO_VIA", "DOMICILIO": "CALLE", "NMVIAL": "NUMERO",
    "CDPOSTAL": "CP", "DAT": "AREA_TERRITORIAL",
})
salida.to_csv("ies_publicos_madrid.csv", index=False, encoding="utf-8")
print(f"\nGuardado ies_publicos_madrid.csv con {len(salida)} IES públicos.")
print(salida["MUNICIPIO"].value_counts().head(15).to_string())
