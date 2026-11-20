"""
Paso 2b (v8): matriz de tiempos puerta a puerta con OTP2 via GraphQL (/otp/gtfs/v1).
v8: normaliza TODAS las unidades al ingerir (itinerary.duration en segundos;
itinerary.startTime/endTime y legs startTime/endTime en milisegundos).
Sin esto, el desglose sale x1000 y la columna salida peta con 'year 58867'.
"""
import requests
import pandas as pd

DIRECCION_CASA = "C. de Fray José Cerdeiriña, 39, Madrid"
FECHA = "2026-11-24"
HORA_LLEGADA = "08:00"
MAX_TOP = 500
IES_CSV = "ies_publicos_madrid.csv"
OTP = "http://localhost:8080"

CATEGORIAS = ["andando", "metro", "bus_urbano", "bus_interurbano", "cercanias", "metro_ligero", "otro"]

def a_segundos(t):
    """Epoch: autodetecta ms vs s por magnitud y devuelve SIEMPRE segundos."""
    if t is None:
        return None
    return t / 1000 if t > 1e11 else t

def normalizar_itinerario(it):
    """Deja el itinerario entero en segundos: duration, startTime/endTime del
    itinerario COMPLETO y de cada leg. La API mezcla unidades: duration en
    segundos, el resto en milisegundos."""
    it["duration"] = it["duration"] / 1000 if it["duration"] > 86400 else it["duration"]
    it["startTime"] = a_segundos(it.get("startTime"))
    it["endTime"] = a_segundos(it.get("endTime"))
    for leg in it["legs"]:
        leg["startTime"] = a_segundos(leg["startTime"])
        leg["endTime"] = a_segundos(leg["endTime"])
    return it

def _json_seguro(r):
    try:
        return r.json()
    except ValueError:
        return None

def geocodificar(direccion):
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": direccion, "format": "json", "limit": 1, "countrycodes": "es"},
                     headers={"User-Agent": "mini-proyecto-ies-madrid/1.0"}, timeout=30)
    res = r.json()
    if not res:
        raise SystemExit(f"No pude geocodificar: {direccion}")
    print(f"Casa: {res[0]['display_name']}  ({res[0]['lat']}, {res[0]['lon']})")
    return float(res[0]["lat"]), float(res[0]["lon"])

QUERY = """query Plan($from: InputCoordinates!, $to: InputCoordinates!, $arriveBy: Boolean!) {
  plan(from: $from, to: $to,
       date: \"%(fecha)s\", time: \"%(hora)s\",
       arriveBy: $arriveBy,
       transportModes: %(modes)s,
       numItineraries: 3) {
    itineraries {
      duration startTime
      legs { mode startTime endTime route { shortName agency { name } } }
    }
  }
}"""

MODO_ENUMS = {"TRANSIT,WALK": "[{mode: TRANSIT}, {mode: WALK}]", "WALK": "[{mode: WALK}]"}

def clasificar_tramo(leg):
    m = leg.get("mode")
    agencia = (((leg.get("route") or {}).get("agency") or {}).get("name") or "").upper()
    if m == "WALK":
        return "andando"
    if m == "SUBWAY":
        return "metro"
    if m == "TRAM":
        return "metro_ligero"
    if m == "RAIL":
        return "cercanias"
    if m == "BUS":
        if "EMT" in agencia or "MUNICIPAL" in agencia:
            return "bus_urbano"
        return "bus_interurbano"
    return "otro"

def desglosar(itinerario):
    d = {c: 0.0 for c in CATEGORIAS}
    for leg in itinerario["legs"]:
        d[clasificar_tramo(leg)] += (leg["endTime"] - leg["startTime"]) / 60
    en_movimiento = sum(l["endTime"] - l["startTime"] for l in itinerario["legs"]) / 60
    espera = max(0, (itinerario["duration"] / 60) - en_movimiento)
    return d, espera

def pedir_graphql(origen, destino, modo, arrive_by=True):
    hora = HORA_LLEGADA if len(HORA_LLEGADA) == 8 else HORA_LLEGADA + ":00"
    query = QUERY % {"fecha": FECHA, "hora": hora, "modes": MODO_ENUMS[modo]}
    body = {"query": query,
            "variables": {"from": {"lat": origen[0], "lon": origen[1]},
                          "to": {"lat": destino[0], "lon": destino[1]},
                          "arriveBy": arrive_by}}
    r = requests.post(f"{OTP}/otp/gtfs/v1", json=body, timeout=120)
    data = _json_seguro(r)
    if r.status_code != 200 or data is None:
        return None
    if data.get("errors"):
        print("  GraphQL errors:", data["errors"][:1])
        return None
    its = ((data.get("data") or {}).get("plan") or {}).get("itineraries") or []
    if not its:
        return None
    return normalizar_itinerario(min(its, key=lambda i: i["duration"]))

def main():
    casa = geocodificar(DIRECCION_CASA)
    prueba = pedir_graphql(casa, (40.4168, -3.7038), "TRANSIT,WALK", arrive_by=True)
    if prueba is None:
        raise SystemExit("La peticion de prueba casa->Puerta del Sol no devolvio itinerarios.")
    print(f"Prueba OK: Sol queda a {prueba['duration']/60:.0f} min de casa\n")

    ies = pd.read_csv(IES_CSV)
    print(f"Calculando tiempos para {len(ies)} IES ...")
    resultados = []
    for n, fila in ies.iterrows():
        destino = (fila["lat"], fila["lon"])
        it = pedir_graphql(casa, destino, "TRANSIT,WALK", arrive_by=True)
        nota = "tp"
        it2 = pedir_graphql(casa, destino, "WALK", arrive_by=True)
        if it2 and it2["duration"] < 2 * 3600:
            if it is None or it2["duration"] < it["duration"]:
                it, nota = it2, "a pie"
        if it is None:
            resultados.append({**fila.to_dict(), "minutos": None, "transbordos": None,
                               "salida": None, "espera_min": None, "nota": "sin ruta",
                               **{f"min_{c}": None for c in CATEGORIAS}})
        else:
            d, espera = desglosar(it)
            resultados.append({**fila.to_dict(),
                               "minutos": round(it["duration"] / 60),
                               "transbordos": sum(1 for l in it["legs"] if l["mode"] != "WALK") - 1,
                               "salida": it.get("startTime"),
                               "espera_min": round(espera),
                               "nota": nota,
                               **{f"min_{c}": round(v) for c, v in d.items()}})
        if (n + 1) % 25 == 0:
            print(f"  {n+1}/{len(ies)} ...")

    df = pd.DataFrame(resultados)
    if df["minutos"].isna().all():
        raise SystemExit(f"NINGUN IES tiene ruta. Revisa que {FECHA} cae en los rangos de validez.")
    df = df.dropna(subset=["minutos"]).sort_values("minutos").head(MAX_TOP).reset_index(drop=True)
    df["salida"] = pd.to_datetime(df["salida"], unit="s", utc=True) \
                  .dt.tz_convert("Europe/Madrid").dt.strftime("%H:%M")
    df.to_csv("ies_por_tiempo_otp.csv", index=False, encoding="utf-8")
    print(f"Sin ruta: {sum(1 for r in resultados if r['minutos'] is None)} IES")
    cols = ["CENTRO", "MUNICIPIO", "minutos", "espera_min"] + [f"min_{c}" for c in CATEGORIAS[:6]]
    print(df[cols].head(30).to_string(index=False))
    print(f"\nGuardado ies_por_tiempo_otp.csv ({len(df)} IES, desglose en minutos reales).")

if __name__ == "__main__":
    main()
