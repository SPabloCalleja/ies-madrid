"""
Paso 3 (v7): visualizador Streamlit del miniproyecto de IES.
- Slider de minutos maximos hasta 3 h (por defecto 57)
- Desplegable y contador sincronizados con los filtros
- Clic en un IES del mapa carga su trayecto
- Desglose por modo (min y km andando), tabla de tramos con badges, GPX
- Boton en la barra lateral: Excel con todos los centros del filtro
  (hojas: Centros, Rutas tramo a tramo, Resumen)

Requisitos: uv add streamlit streamlit-folium plotly openpyxl
Ejecutar:   uv run streamlit run app_ies.py   (contenedor OTP arriba)
"""
import datetime
import html
import io
import math
import requests
import pandas as pd
import folium
import branca.colormap
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

# ============ CONFIGURACION ============
DIRECCION_CASA = "C. de Fray José Cerdeiriña, 39, Madrid, Madrid"
FECHA = "2026-11-24"
HORA_LLEGADA = "08:00"
IES_CSV = "ies_por_tiempo_otp.csv"
OTP = "http://localhost:8080"
# =======================================

QUERY_MAPA = """query Plan($from: InputCoordinates!, $to: InputCoordinates!, $arriveBy: Boolean!) {
  plan(from: $from, to: $to, date: \"%(fecha)s\", time: \"%(hora)s\",
       arriveBy: $arriveBy, transportModes: [{mode: TRANSIT}, {mode: WALK}], numItineraries: 3) {
    itineraries {
      duration startTime endTime
      legs {
        mode startTime endTime distance
        route { shortName agency { name } }
        from { name } to { name }
        legGeometry { points }
      }
    }
  }
}"""

COLOR_MODO = {"andando": "#3b7dd8", "bus_urbano": "#d84b3b", "bus_interurbano": "#2ca02c",
              "metro": "#1b7a4a", "cercanias": "#8e44ad", "metro_ligero": "#f39c12",
              "espera": "#999999", "otro": "#555555"}
ETIQUETA_MODO = {"andando": "andando", "bus_urbano": "bus urbano", "bus_interurbano": "bus interurbano",
                 "metro": "metro", "cercanias": "cercanias", "metro_ligero": "metro ligero",
                 "espera": "espera", "otro": "otro"}
EMOJI_MODO = {"andando": "🚶", "bus_urbano": "🚌", "bus_interurbano": "🚌", "metro": "🚇",
              "cercanias": "🚆", "metro_ligero": "🚊", "espera": "⏱", "otro": "•"}
ORDEN_CATEGORIAS = ["andando", "metro", "bus_urbano", "bus_interurbano", "cercanias", "metro_ligero", "otro"]

COLUMNAS_EXCEL = [
    ("CENTRO", "IES"),
    ("minutos", "minutos"),
    ("transbordos", "transbordos"),
    ("salida", "salida de casa"),
    ("espera_min", "espera (min)"),
    ("min_andando", "min andando"),
    ("min_metro", "min metro"),
    ("min_bus_urbano", "min bus urbano"),
    ("min_bus_interurbano", "min bus interurbano"),
    ("min_cercanias", "min cercanias"),
    ("min_metro_ligero", "min metro ligero"),
    ("MUNICIPIO", "municipio"),
    ("CALLE", "calle"),
    ("NUMERO", "numero"),
    ("CP", "cp"),
    ("AREA_TERRITORIAL", "area territorial"),
    ("TELEFONO", "telefono"),
    ("WEB", "web"),
    ("E_MAIL", "email"),
    ("lat", "lat"),
    ("lon", "lon"),
    ("nota", "nota"),
]

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

def decodificar_polyline(codificada):
    puntos, i, lat, lng = [], 0, 0, 0
    while i < len(codificada):
        for es_lat in (True, False):
            resultado, shift = 0, 0
            while True:
                b = ord(codificada[i]) - 63
                i += 1
                resultado |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(resultado >> 1) if resultado & 1 else resultado >> 1
            if es_lat:
                lat += delta
            else:
                lng += delta
        puntos.append((lat / 1e5, lng / 1e5))
    return puntos

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

def hora_local(epoch_s):
    from zoneinfo import ZoneInfo
    if not epoch_s:
        return "--:--"
    return (datetime.datetime.fromtimestamp(epoch_s, tz=datetime.timezone.utc)
            .astimezone(ZoneInfo("Europe/Madrid"))).strftime("%H:%M")

def desglosar(itinerario):
    d = {c: 0.0 for c in ORDEN_CATEGORIAS}
    km_andando = 0.0
    for leg in itinerario["legs"]:
        cat = clasificar_tramo(leg)
        d[cat] += (leg["endTime"] - leg["startTime"]) / 60
        if cat == "andando" and leg.get("distance"):
            km_andando += leg["distance"] / 1000
    en_movimiento = sum(l["endTime"] - l["startTime"] for l in itinerario["legs"]) / 60
    espera = max(0, (itinerario["duration"] / 60) - en_movimiento)
    return d, espera, km_andando

def ies_mas_cercano(vis, lat, lng, umbral_m=400):
    mejor, mejor_d = None, float("inf")
    for _, f in vis.iterrows():
        dx = (f["lon"] - lng) * 111320 * math.cos(math.radians(lat))
        dy = (f["lat"] - lat) * 110540
        dist = math.hypot(dx, dy)
        if dist < mejor_d:
            mejor, mejor_d = f["etiqueta"], dist
    return mejor if mejor_d <= umbral_m else None

@st.cache_data(show_spinner="Geocodificando la vivienda...")
def geocodificar(direccion):
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": direccion, "format": "json", "limit": 1, "countrycodes": "es"},
                     headers={"User-Agent": "mini-proyecto-ies-madrid/1.0"}, timeout=30)
    j = r.json()[0]
    return float(j["lat"]), float(j["lon"])

@st.cache_data
def cargar_ies():
    df = pd.read_csv(IES_CSV)
    df["etiqueta"] = df["CENTRO"] + " (" + df["MUNICIPIO"] + ")"
    return df.sort_values("minutos")

@st.cache_data(ttl=600, show_spinner=False)
def pedir_itinerario(casa, destino):
    body = {"query": QUERY_MAPA % {"fecha": FECHA, "hora": HORA_LLEGADA + ":00"},
            "variables": {"from": {"lat": casa[0], "lon": casa[1]},
                          "to": {"lat": destino[0], "lon": destino[1]}, "arriveBy": True}}
    r = requests.post(f"{OTP}/otp/gtfs/v1", json=body, timeout=120)
    data = r.json()
    if data.get("errors"):
        return None
    its = ((data.get("data") or {}).get("plan") or {}).get("itineraries") or []
    return normalizar_itinerario(min(its, key=lambda i: i["duration"])) if its else None

def filas_rutas_de(itinerario, centro, min_total):
    filas = []
    prev_fin = None
    for leg in itinerario["legs"]:
        espera = max(0, (leg["startTime"] - prev_fin) / 60) if prev_fin else 0
        filas.append({
            "IES": centro, "minutos total": min_total,
            "modo": ETIQUETA_MODO[clasificar_tramo(leg)],
            "linea": (leg.get("route") or {}).get("shortName") or "",
            "sale": hora_local(leg["startTime"]), "llega": hora_local(leg["endTime"]),
            "desde": (leg.get("from") or {}).get("name") or "",
            "hasta": (leg.get("to") or {}).get("name") or "",
            "duracion (min)": round((leg["endTime"] - leg["startTime"]) / 60),
            "km": round((leg.get("distance") or 0) / 1000, 1),
            "espera previa (min)": round(espera),
        })
        prev_fin = leg["endTime"]
    return filas

def generar_excel(casa, vis, max_min):
    centros = vis.copy()
    columnas = [(c, alias) for c, alias in COLUMNAS_EXCEL if c in centros.columns]
    centros = centros[[c for c, _ in columnas]]
    centros.columns = [alias for _, alias in columnas]

    todas_rutas = []
    barra = st.progress(0.0, text="Pidiendo itinerarios a OTP...")
    for i, (_, fila) in enumerate(vis.iterrows()):
        it = pedir_itinerario(casa, (fila["lat"], fila["lon"]))
        if it:
            todas_rutas += filas_rutas_de(it, fila["CENTRO"], fila["minutos"])
        barra.progress((i + 1) / len(vis), text=f"Itinerarios: {i + 1}/{len(vis)}")
    rutas = pd.DataFrame(todas_rutas)

    resumen = pd.DataFrame({
        "parametro": ["vivienda", "fecha calculo", "hora de llegada", "filtro minutos max",
                      "centros incluidos", "generado"],
        "valor": [DIRECCION_CASA, FECHA, HORA_LLEGADA, max_min, len(vis),
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")],
    })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        centros.to_excel(xw, sheet_name="Centros", index=False)
        rutas.to_excel(xw, sheet_name="Rutas", index=False)
        resumen.to_excel(xw, sheet_name="Resumen", index=False)
        from openpyxl.utils import get_column_letter
        for hoja in ("Centros", "Rutas", "Resumen"):
            ws = xw.sheets[hoja]
            for n_col in range(1, ws.max_column + 1):
                ancho = max(len(str(ws.cell(1, n_col).value or "")), 10) + 4
                ws.column_dimensions[get_column_letter(n_col)].width = min(ancho, 45)
    return buf.getvalue()

def nombre_leg(leg):
    cat = clasificar_tramo(leg)
    linea = (leg.get("route") or {}).get("shortName")
    if cat == "andando" or not linea:
        return ETIQUETA_MODO[cat]
    return f"{ETIQUETA_MODO[cat]} {linea}"

def badge(leg):
    cat = clasificar_tramo(leg)
    return (f'<span style="background-color:{COLOR_MODO[cat]};color:white;padding:3px 10px;'
            f'border-radius:12px;font-size:0.85em;white-space:nowrap">'
            f'{EMOJI_MODO[cat]} {html.escape(nombre_leg(leg))}</span>')

def tabla_tramos_html(it):
    filas = []
    for leg in it["legs"]:
        st_, et_ = leg.get("startTime"), leg.get("endTime")
        dur = (et_ - st_) / 60
        dist = leg.get("distance")
        filas.append(
            f"<tr>"
            f"<td style='padding:4px 10px'>{hora_local(st_)}</td>"
            f"<td style='padding:4px 10px'>{badge(leg)}</td>"
            f"<td style='padding:4px 10px'>{html.escape((leg.get('from') or {}).get('name') or '-')}</td>"
            f"<td style='padding:4px 10px'>{html.escape((leg.get('to') or {}).get('name') or '-')}</td>"
            f"<td style='padding:4px 10px;text-align:right'>{dur:.0f} min</td>"
            f"<td style='padding:4px 10px;text-align:right'>{dist/1000:.1f} km</td>"
            f"<td style='padding:4px 10px;text-align:right'>{hora_local(et_)}</td>"
            f"</tr>")
    cab = ("<tr style='text-align:left'>"
           "<th style='padding:4px 10px'>sale</th><th>tramo</th><th>desde</th><th>hasta</th>"
           "<th style='text-align:right'>dura</th><th style='text-align:right'>dist</th>"
           "<th style='text-align:right'>llega</th></tr>")
    return ("<table style='border-collapse:collapse;font-size:0.95em'>"
            + cab + "".join(filas) + "</table>")

def gpx_de_itinerario(it):
    lineas = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<gpx version="1.1" creator="mini-ies-madrid"',
              ' xmlns="http://www.topografix.com/GPX/1/1">']
    for leg in it["legs"]:
        pts = decodificar_polyline(leg["legGeometry"]["points"])
        if not pts:
            continue
        lineas.append(f"<trk><name>{html.escape(nombre_leg(leg))}</name><trkseg>")
        for lat, lon in pts:
            lineas.append(f'<trkpt lat="{lat:.5f}" lon="{lon:.5f}"></trkpt>')
        lineas.append("</trkseg></trk>")
    lineas.append("</gpx>")
    return "\n".join(lineas)

def main():
    st.set_page_config(layout="wide")
    st.title("IES publicos de Madrid por tiempo de transporte")
    st.caption(f"Desde la vivienda, llegando a las {HORA_LLEGADA} ({FECHA}, planificacion GTFS, sin tiempo real)")

    casa = geocodificar(DIRECCION_CASA)
    df = cargar_ies()
    if df.empty:
        st.error("No encuentro ies_por_tiempo_otp.csv: ejecuta antes matriz_tiempos_otp.py")
        return

    with st.sidebar:
        st.markdown("### Filtros")
        max_min = st.slider("Minutos maximos", int(df["minutos"].min()), 180, 57, 5)
        solo_a_pie_ok = st.checkbox("Incluir los que van mejor a pie", True)
        sel_muni = st.multiselect("Municipios", sorted(df["MUNICIPIO"].unique()))

    vis = df[df["minutos"] <= max_min]
    if not solo_a_pie_ok:
        vis = vis[vis["nota"] != "a pie"]
    if sel_muni:
        vis = vis[vis["MUNICIPIO"].isin(sel_muni)]
    st.sidebar.success(f"**{len(vis)} centros** en el mapa")

    # --- Exportar a Excel ---
    if st.sidebar.button("Generar Excel de los centros", type="primary", disabled=vis.empty):
        st.session_state["excel_bytes"] = generar_excel(casa, vis, max_min)
        st.session_state["excel_filtro"] = max_min
    if st.session_state.get("excel_bytes"):
        st.sidebar.download_button(
            "Descargar Excel", st.session_state["excel_bytes"],
            file_name=f"ies_{max_min}min_{FECHA}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help=f"Centros hasta {st.session_state.get('excel_filtro', max_min)} min, con rutas detalladas")
    else:
        st.sidebar.caption("El Excel pide a OTP el itinerario de cada centro visible "
                           "(los ya consultados salen de cache); tarda un poco la primera vez.")

    opciones = vis["etiqueta"].tolist()
    if st.session_state.get("seleccion") not in set(opciones):
        st.session_state["seleccion"] = opciones[0]
    etiqueta = st.sidebar.selectbox("IES a detallar", opciones,
                                    index=opciones.index(st.session_state["seleccion"]))
    st.session_state["seleccion"] = etiqueta

    d = vis[vis["etiqueta"] == etiqueta].iloc[0]
    it = pedir_itinerario(casa, (d["lat"], d["lon"]))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tiempo total", f"{d['minutos']} min")
    col2.metric("Transbordos", d["transbordos"])
    col3.metric("Salida de casa", d["salida"])

    if it:
        dsc, espera, km_andando = desglosar(it)
        col4.metric("Esperas en paradas", f"{espera:.0f} min")

        c_izq, c_der = st.columns([2, 3])
        with c_izq:
            st.subheader("Desglose por modo")
            filas = []
            for c in ORDEN_CATEGORIAS:
                if dsc[c] > 0.5:
                    etiqueta_txt = ETIQUETA_MODO[c]
                    if c == "andando":
                        etiqueta_txt += f" ({km_andando:.1f} km)"
                    filas.append({"modo": ETIQUETA_MODO[c], "etiqueta": etiqueta_txt,
                                  "minutos": round(dsc[c])})
            if espera > 0.5:
                filas.append({"modo": "espera", "etiqueta": "espera", "minutos": round(espera)})
            desglose = pd.DataFrame(filas)
            fig = px.bar(desglose, x="minutos", y="etiqueta", orientation="h", color="modo",
                         color_discrete_map=COLOR_MODO,
                         text=desglose["minutos"].astype(str) + " min")
            fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=0, b=0),
                              height=40 + 38 * len(desglose))
            fig.update_yaxes(title=None)
            st.plotly_chart(fig, use_container_width=True)
        with c_der:
            st.subheader(f"Trayecto completo: {d['CENTRO']}")
            st.markdown(tabla_tramos_html(it), unsafe_allow_html=True)
        st.download_button("Descargar trayecto (GPX)", gpx_de_itinerario(it),
                           file_name=f"trayecto_{d['CENTRO'].replace(' ', '_')}.gpx",
                           mime="application/gpx")

    st.markdown(f"Mostrando **{len(vis)} centros** en el mapa (hasta {max_min} min) · "
                f"pincha en uno para cargar su trayecto")
    escala = branca.colormap.LinearColormap(
        ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
        vmin=int(df["minutos"].min()), vmax=int(df["minutos"].max()),
        caption="minutos de viaje (llegada %s)" % HORA_LLEGADA)
    mapa = folium.Map(location=[casa[0] + 0.02, casa[1]], zoom_start=12, tiles="OpenStreetMap")
    folium.Marker(casa, tooltip="Casa", icon=folium.Icon(color="black", icon="home")).add_to(mapa)
    for _, fila in vis.iterrows():
        folium.CircleMarker(
            (fila["lat"], fila["lon"]), radius=6, color=escala(fila["minutos"]),
            fill=True, fill_opacity=0.9,
            tooltip="clic para cargar el trayecto",
            popup=folium.Popup(f"{fila['CENTRO']}<br>{fila['MUNICIPIO']}<br>"
                               f"{fila['minutos']} min · {fila['transbordos']} transbordos<br>"
                               f"salida {fila['salida']} ({fila['nota']})", max_width=250),
        ).add_to(mapa)
    escala.add_to(mapa)

    if it:
        for leg in it["legs"]:
            pts = decodificar_polyline(leg["legGeometry"]["points"])
            if not pts:
                continue
            cat = clasificar_tramo(leg)
            folium.PolyLine(pts, color=COLOR_MODO[cat],
                            weight=4 if cat != "andando" else 3,
                            dash_array="6,8" if cat == "andando" else None,
                            tooltip=f"{nombre_leg(leg)} {hora_local(leg['startTime'])} -> "
                                    f"{hora_local(leg['endTime'])}").add_to(mapa)

    try:
        respuesta = st_folium(mapa, height=650, use_container_width=True,
                              returned_objects=["last_object_clicked"])
    except TypeError:
        respuesta = st_folium(mapa, height=650, use_container_width=True)

    click = (respuesta or {}).get("last_object_clicked")
    if click:
        clave = (round(click["lat"], 5), round(click["lng"], 5))
        if st.session_state.get("click_previo") != clave:
            st.session_state["click_previo"] = clave
            cercano = ies_mas_cercano(vis, click["lat"], click["lng"])
            if cercano and cercano != etiqueta:
                st.session_state["seleccion"] = cercano
                try:
                    st.rerun()
                except AttributeError:
                    st.experimental_rerun()

if __name__ == "__main__":
    main()
