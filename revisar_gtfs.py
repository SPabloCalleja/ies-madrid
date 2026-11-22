"""
Revisa los GTFS .zip de la carpeta actual (la que montas en el contenedor OTP)
e imprime el rango de fechas de servicio de cada feed, para ver cual esta
caducado respecto a la fecha de calculo del proyecto (2026-11-24).

Uso: copialo a la carpeta de los GTFS y ejecuta:  uv run revisar_gtfs.py
"""
import glob
import io
import sys
import zipfile

import pandas as pd

FECHA_OBJETIVO = "20261124"


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


def main():
    zips = sorted(glob.glob("*.zip"))
    if not zips:
        print("No hay .zip en esta carpeta")
        return
    print(f"Fecha de calculo del proyecto: {FECHA_OBJETIVO[:4]}-{FECHA_OBJETIVO[4:6]}-{FECHA_OBJETIVO[6:]}\n")
    caducados = []
    for z in zips:
        try:
            with zipfile.ZipFile(z) as f:
                if "agency.txt" not in f.namelist():
                    continue
                ag, inicio, fin = rango_feed(f)
        except zipfile.BadZipFile:
            print(f"{z:40} zip corrupto")
            continue
        if fin is None:
            print(f"{z:40} {ag[:25]:27} sin calendario")
            continue
        estado = "OK" if fin >= FECHA_OBJETIVO else "CADUCADO <---"
        if fin < FECHA_OBJETIVO:
            caducados.append((z, ag, fin))
        print(f"{z:40} {ag[:25]:27} {inicio} -> {fin}  {estado}")
    if caducados:
        print("\nFeeds que NO cubren la fecha y hay que re-descargar del portal")
        print("de datos abiertos del CRTM (datos.crtm.es):")
        for z, ag, fin in caducados:
            print(f"  - {z} ({ag}): servicio termina {fin}")
    else:
        print("\nTodos los feeds cubren la fecha: el problema seria otro")


if __name__ == "__main__":
    main()
