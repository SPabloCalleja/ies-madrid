"""
Diagnostico de modos: comprueba que OTP usa bien metro y buses y aclara por
que Cercanias domina los trayectos. No modifica nada, solo imprime.

Que hace:
  1) Lista las agencias cargadas en el router y sus rutas por modo
     (si falta Metro de Madrid o EMT, hay problema de datos).
  2) Para la vivienda y la hora objetivo, pide el mejor itinerario hacia
     destinos de control (Sol, Atocha, Moncloa...) usando SOLO cada modo
     (metro / bus / cercanias) y tambien libre, para comparar tiempos.
  3) Para los 20 centros mas rapidos del CSV: mejor trayecto libre vs mejor
     trayecto SIN Renfe, para ver cuanto costaria prescindir de Cercanias.
  4) Estadisticas del CSV: media de minutos por modo y % de centros que
     usan cada modo.

Uso: uv run diagnostico_modos.py   (contenedor OTP arriba)
"""
import requests
import pandas as pd

DIRECCION_CASA = "C. de Fray José Cerdeiriña, 39, Madrid"
FECHA = "2026-11-24"
HORA_LLEGADA = "08:00"
IES_CSV = "ies_por_tiempo_otp.csv"
OTP = "http://localhost:8080"

QUERY = """query Plan($from: InputCoordinates!, $to: InputCoordinates!, $arriveBy: Boolean!) {
  plan(from: $from, to: $to, date: \"%(fecha)s\", time: \"%(hora)s\",
       arriveBy: $arriveBy,
       %(ban)s
       transportModes: %(modes)s, numItineraries: 3) {
    itineraries { duration legs { mode route { shortName agency { gtfsId name } } } }
  }
}"""

QUERY_RUTAS = "query { routes { gtfsId shortName mode agency { gtfsId name } } }"
QUERY_AGENCIAS = "query { agencies { gtfsId name routes { shortName mode } } }"

MODO_SETS = {
    "libre (transit)": "[{mode: TRANSIT}, {mode: WALK}]",
    "solo metro": "[{mode: SUBWAY}, {mode: WALK}]",
    "solo bus": "[{mode: BUS}, {mode: WALK}]",
    "solo cercanias": "[{mode: RAIL}, {mode: WALK}]",
    "solo tranvia": "[{mode: TRAM}, {mode: WALK}]",
}

DESTINOS_CONTROL = {
    "Puerta del Sol": (40.41689, -3.70341),
    "Atocha": (40.40827, -3.69138),
    "Nuevos Ministerios": (40.44627, -3.69219),
    "Moncloa": (40.43952, -3.73248),
    "Plaza de Castilla": (40.46644, -3.68941),
}


def geocodificar(direccion):
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": direccion, "format": "json", "limit": 1, "countrycodes": "es"},
                     headers={"User-Agent": "mini-proyecto-ies-madrid/1.0"}, timeout=30)
    j = r.json()[0]
    return float(j["lat"]), float(j["lon"])


def graphql(query, variables=None):
    r = requests.post(f"{OTP}/otp/gtfs/v1", json={"query": query, "variables": variables or {}},
                      timeout=120)
    data = r.json()
    if data.get("errors"):
        return None
    return data.get("data")


def resumen_itinerario(it):
    """'C-5 12 min + andar 8' con desglose por modo."""
    totales, lineas = {}, []
    for leg in it["legs"]:
        m = leg["mode"]
        if m == "WALK":
            continue
        totales[m] = totales.get(m, 0) + 1
        linea = (leg.get("route") or {}).get("shortName") or m
        if linea not in lineas:
            lineas.append(linea)
    dur = it["duration"] / 60 if it["duration"] > 86400 else it["duration"] / 60
    cuerpo = " + ".join(lineas) if lineas else "todo a pie"
    return f"{dur:.0f} min [{cuerpo}]"


def pedir(origen, destino, modes, ban=()):
    ban_s = 'banned: {agencies: "%s"},' % ",".join(sorted(ban)) if ban else ""
    data = graphql(QUERY % {"fecha": FECHA, "hora": HORA_LLEGADA + ":00",
                            "modes": modes, "ban": ban_s},
                   {"from": {"lat": origen[0], "lon": origen[1]},
                    "to": {"lat": destino[0], "lon": destino[1]}, "arriveBy": True})
    its = ((data or {}).get("plan") or {}).get("itineraries") or []
    return min(its, key=lambda i: i["duration"]) if its else None


def agencias_y_renfe():
    data = graphql(QUERY_RUTAS) or graphql(QUERY_AGENCIAS)
    renfe = set()
    if data and "routes" in data:
        df = pd.DataFrame(data["routes"])
        df["agencia"] = df["agency"].apply(lambda a: a["name"])
        tabla = df.groupby(["agencia", "mode"]).size().unstack(fill_value=0)
        print("Rutas cargadas en el router, por agencia y modo:")
        print(tabla.to_string())
        renfe = {a["gtfsId"] for a in (df[df["mode"] == "RAIL"]["agency"])}
    elif data and "agencies" in data:
        filas = []
        for ag in data["agencies"]:
            df = pd.DataFrame(ag.get("routes") or [])
            if df.empty:
                filas.append((ag["name"], 0, 0, 0))
                continue
            conteo = df["mode"].value_counts().to_dict()
            filas.append((ag["name"], conteo.get("BUS", 0), conteo.get("SUBWAY", 0),
                          conteo.get("RAIL", 0)))
            if conteo.get("RAIL", 0):
                renfe.add(ag["gtfsId"])
        tabla = pd.DataFrame(filas, columns=["agencia", "rutas bus", "rutas metro",
                                             "rutas rail"])
        print("Agencias cargadas en el router:")
        print(tabla.to_string(index=False))
    else:
        print("No pude consultar agencias/rutas al router")
    return renfe


def main():
    casa = geocodificar(DIRECCION_CASA)
    print(f"Casa: {casa[0]:.5f}, {casa[1]:.5f} · llegada {HORA_LLEGADA} del {FECHA}\n")

    print("=" * 72)
    print("1) DATOS CARGADOS EN OTP")
    print("=" * 72)
    renfe = agencias_y_renfe()
    if not renfe:
        print("AVISO: no he identificado agencia de RAIL (Renfe/Cercanias)")
    else:
        print(f"Agencias de rail a vetear si excluyeramos Cercanias: {sorted(renfe)}")

    print()
    print("=" * 72)
    print("2) DESTINOS DE CONTROL: mejor trayecto forzando cada modo")
    print("=" * 72)
    for nombre, destino in DESTINOS_CONTROL.items():
        print(f"\n{nombre}:")
        for etiqueta, modes in MODO_SETS.items():
            it = pedir(casa, destino, modes)
            if it is None:
                print(f"   {etiqueta:18} SIN RUTA  <-- sospechoso si es metro o bus")
            else:
                print(f"   {etiqueta:18} {resumen_itinerario(it)}")

    print()
    print("=" * 72)
    print("3) 20 CENTROS MAS RAPIDOS: libre vs SIN Cercanias")
    print("=" * 72)
    try:
        ies = pd.read_csv(IES_CSV).head(20)
    except FileNotFoundError:
        print(f"No encuentro {IES_CSV}: salta esta parte")
        ies = []
    usan = 0
    for _, fila in ies.iterrows():
        destino = (fila["lat"], fila["lon"])
        libre = pedir(casa, destino, MODO_SETS["libre (transit)"])
        sin = pedir(casa, destino, MODO_SETS["libre (transit)"], ban=renfe) if renfe else None
        t_libre = libre["duration"] / 60 if libre else float("inf")
        t_sin = sin["duration"] / 60 if sin else float("inf")
        if sin is not None:
            usan += 1
        tipo = fila.get("TIPO_ABRV", "")
        print(f"  {tipo} {fila['CENTRO'][:38]:40} "
              f"libre {t_libre:5.0f} min | sin cercanias "
              f"{t_sin if sin else '-':>5} min | +{t_sin - t_libre:4.0f} min"
              if sin else
              f"  {tipo} {fila['CENTRO'][:38]:40} libre {t_libre:5.0f} min | SIN RUTA sin cercanias")

    print()
    print("=" * 72)
    print("4) LO QUE DICE TU CSV (matriz ya calculada)")
    print("=" * 72)
    try:
        df = pd.read_csv(IES_CSV)
        cols = [c for c in df.columns if c.startswith("min_")]
        print("Media de minutos por modo en el mejor trayecto de cada centro:")
        print(df[cols].mean().round(1).sort_values(ascending=False).to_string())
        print("\nPorcentaje de centros cuyo mejor trayecto usa cada modo:")
        for c in cols:
            print(f"   {c:22} {100 * (df[c] > 0).mean():5.1f} %")
    except FileNotFoundError:
        print("No encuentro el CSV")

    print("\nComo leerlo: si en la seccion 2 el metro y el bus dan ruta a los")
    print("destinos de control, OTP esta bien: Cercanias gana porque es mas")
    print("rapido puerta a puerta (la app muestra el MAS RAPIDO). Si en la")
    print("seccion 4 min_metro/min_bus_urbano tienen media > 0, metro y bus se")
    print("usan como tramos de acceso. Si algo sale SIN RUTA o 0 %, ahi hay")
    print("bug de datos (GTFS caducado) y lo miramos.")


if __name__ == "__main__":
    main()
