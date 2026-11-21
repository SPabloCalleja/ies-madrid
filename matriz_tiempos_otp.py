"""
Paso 2b (v11): matriz de tiempos puerta a puerta con OTP2 via GraphQL (/otp/gtfs/v1).
v11: sin filtro de ensenanzas: los candidatos son por TIPO de centro
     (IES/IESO/CEPA/CP INF-PRI-SEC, decididos en descargar_centros.py).
v10/v9: paradas_cercanias en batch (intermediateStops) y rutas sin buses
     interurbanos (banned agencies con reintento si aparece otra agencia).
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

QUERY = """query Plan($from: InputCoordinates!, $to: InputCoordinates!, $arriveBy: Boolean!) {
  plan(from: $from, to: $to,
       date: \"%(fecha)s\", time: \"%(hora)s\",
       arriveBy: $arriveBy,
       %(ban)s
       transportModes: %(modes)s,
       numItineraries: 3) {
    itineraries {
      duration startTime
      legs {
        mode startTime endTime
        route { shortName agency { gtfsId name } }
        intermediateStops { name }
      }
    }
  }
}"""

MODO_ENUMS = {"TRANSIT,WALK": "[{mode: TRANSIT}, {mode: WALK}]", "WALK": "[{mode: WALK}]"}


def a_segundos(t):
    if t is None:
        return None
    return t / 1000 if t > 1e11 else t


def normalizar_itinerario(it):
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
    print(f"Casa: {res[0]['display_name']} ({res[0]['lat']}, {res[0]['lon']})")
    return float(res[0]["lat"]), float(res[0]["lon"])


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


def paradas_cercanias(itinerario):
    total = 0
    for leg in (itinerario or {}).get("legs", []):
        if clasificar_tramo(leg) == "cercanias":
            total += len(leg.get("intermediateStops") or []) + 2
    return total


def agencias_interurbanas(itinerario):
    ids = set()
    for leg in (itinerario or {}).get("legs", []):
        if clasificar_tramo(leg) == "bus_interurbano":
            agencia = (leg.get("route") or {}).get("agency") or {}
            ident = agencia.get("gtfsId") or agencia.get("id")
            if ident:
                ids.add(ident)
    return ids


def _cadena_ban(agencias):
    if not agencias:
        return ""
    return 'banned: {agencies: "%s"},' % ",".join(sorted(agencias))


def pedir_graphql(origen, destino, modo, arrive_by=True, agencias_ban=None):
    hora = HORA_LLEGADA if len(HORA_LLEGADA) == 8 else HORA_LLEGADA + ":00"
    query = QUERY % {"fecha": FECHA, "hora": hora, "modes": MODO_ENUMS[modo],
                     "ban": _cadena_ban(agencias_ban)}
    body = {"query": query,
            "variables": {"from": {"lat": origen[0], "lon": origen[1]},
                          "to": {"lat": destino[0], "lon": destino[1]},
                          "arriveBy": arrive_by}}
    r = requests.post(f"{OTP}/otp/gtfs/v1", json=body, timeout=120)
    data = _json_seguro(r)
    if r.status_code != 200 or data is None:
        return None
    if data.get("errors"):
        print(" GraphQL errors:", data["errors"][:1])
        return None
    its = ((data.get("data") or {}).get("plan") or {}).get("itineraries") or []
    if not its:
        return None
    return normalizar_itinerario(min(its, key=lambda i: i["duration"]))


def sin_interurbanos(origen, destino, it_normal):
    ban = agencias_interurbanas(it_normal)
    if not ban:
        return None, []
    for _ in range(3):
        it_alt = pedir_graphql(origen, destino, "TRANSIT,WALK", arrive_by=True, agencias_ban=ban)
        if it_alt is None:
            return None, sorted(ban)
        nuevas = agencias_interurbanas(it_alt) - ban
        if not nuevas:
            return it_alt, sorted(ban)
        ban |= nuevas
    return None, sorted(ban)


def main():
    casa = geocodificar(DIRECCION_CASA)
    prueba = pedir_graphql(casa, (40.4168, -3.7038), "TRANSIT,WALK", arrive_by=True)
    if prueba is None:
        raise SystemExit("La peticion de prueba casa->Puerta del Sol no devolvio itinerarios.")
    print(f"Prueba OK: Sol queda a {prueba['duration']/60:.0f} min de casa\n")

    ies = pd.read_csv(IES_CSV)
    print(f"Calculando tiempos para {len(ies)} centros candidatos ...")
    resultados = []
    for n, fila in ies.iterrows():
        destino = (fila["lat"], fila["lon"])
        it = pedir_graphql(casa, destino, "TRANSIT,WALK", arrive_by=True)
        nota = "tp"
        it2 = pedir_graphql(casa, destino, "WALK", arrive_by=True)
        if it2 and it2["duration"] < 2 * 3600:
            if it is None or it2["duration"] < it["duration"]:
                it, nota = it2, "a pie"

        extra = {"usa_interurbano": False, "minutos_sin_interurbanos": None,
                 "paradas_cercanias_sin_interurbanos": None,
                 "transbordos_sin_interurbanos": None,
                 "salida_sin_interurbanos": None, "agencias_interurbanas": ""}

        if it is None:
            resultados.append({**fila.to_dict(), "minutos": None, "transbordos": None,
                               "salida": None, "espera_min": None, "nota": "sin ruta",
                               "paradas_cercanias": None,
                               **{f"min_{c}": None for c in CATEGORIAS}, **extra})
        else:
            d, espera = desglosar(it)
            if nota != "a pie":
                it_sin, ban = sin_interurbanos(casa, destino, it)
                if ban:
                    extra["usa_interurbano"] = True
                    extra["agencias_interurbanas"] = ",".join(ban)
                    if it_sin is not None:
                        extra.update({
                            "minutos_sin_interurbanos": round(it_sin["duration"] / 60),
                            "paradas_cercanias_sin_interurbanos": paradas_cercanias(it_sin),
                            "transbordos_sin_interurbanos": sum(
                                1 for l in it_sin["legs"] if l["mode"] != "WALK") - 1,
                            "salida_sin_interurbanos": it_sin.get("startTime"),
                        })
            resultados.append({**fila.to_dict(),
                               "minutos": round(it["duration"] / 60),
                               "transbordos": sum(1 for l in it["legs"] if l["mode"] != "WALK") - 1,
                               "salida": it.get("startTime"),
                               "espera_min": round(espera),
                               "nota": nota,
                               "paradas_cercanias": paradas_cercanias(it),
                               **{f"min_{c}": round(v) for c, v in d.items()},
                               **extra})
        if (n + 1) % 25 == 0:
            print(f"  {n+1}/{len(ies)} ...")

    df = pd.DataFrame(resultados)
    if df["minutos"].isna().all():
        raise SystemExit(f"NINGUN centro tiene ruta. Revisa que {FECHA} cae en los rangos de validez.")
    df = df.dropna(subset=["minutos"]).sort_values("minutos").head(MAX_TOP).reset_index(drop=True)
    for col in ("salida", "salida_sin_interurbanos"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="s", utc=True) \
                .dt.tz_convert("Europe/Madrid").dt.strftime("%H:%M")
    df.to_csv("ies_por_tiempo_otp.csv", index=False, encoding="utf-8")

    print(f"Sin ruta: {sum(1 for r in resultados if r['minutos'] is None)} centros")
    con_interurbano = df["usa_interurbano"] == True
    con_alternativa = df["minutos_sin_interurbanos"].notna() & con_interurbano
    print(f"Usan bus interurbano: {con_interurbano.sum()} centros "
          f"({int(con_alternativa.sum())} con alternativa sin ellos, "
          f"{int((con_interurbano & ~con_alternativa).sum())} sin alternativa)")
    cols = ["CENTRO", "TIPO_ABRV", "MUNICIPIO", "minutos", "espera_min", "paradas_cercanias"] + \
           [f"min_{c}" for c in CATEGORIAS[:6]]
    cols = [c for c in cols if c in df.columns]
    print(df[cols].head(30).to_string(index=False))
    print(f"\nGuardado ies_por_tiempo_otp.csv ({len(df)} centros, desglose en minutos reales).")

if __name__ == "__main__":
    main()
