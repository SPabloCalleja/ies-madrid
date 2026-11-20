"""
Paso 1b: enriquecer los IES con ensenanzas y turnos desde el Buscador de
centros de la Comunidad de Madrid (Servicio Web a Padres de Alumnos).
Para cada CODIGO descarga su ficha (MostrarFichaCentro.icm?cdCentro=...) y
extrae: ESO, Bachillerato, FP, vespertino, nocturno, bilingue, jornada.

Fuente: https://gestiona.comunidad.madrid/wpad_pub/run/j/MostrarFichaCentro.icm
Requisitos: uv add requests pandas
Uso: uv run descargar_ensenanzas.py            # procesa los que falten (reanudable)
     uv run descargar_ensenanzas.py --todos    # rehace todos
"""
import re
import sys
import time
import requests
import pandas as pd

FICHA = "https://gestiona.comunidad.madrid/wpad_pub/run/j/MostrarFichaCentro.icm"
IES_CSV = "ies_publicos_madrid.csv"
SALIDA = "ies_caracteristicas.csv"
ESPERA_S = 0.25

def _sesion():
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "Mozilla/5.0 (mini-proyecto-ies-madrid/1.0)"
    return s

def _texto(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

def _filas(seccion_html):
    return [_texto(f) for f in re.findall(r"<tr[^>]*>(.*?)</tr>", seccion_html, re.S | re.I)]

def _seccion(html, desde, hasta):
    m = re.search(desde + r"(.*?)" + hasta, html, re.S | re.I)
    return m.group(1) if m else ""

def _marcado(seccion_html, patron):
    """True si la CELDA que contiene 'patron' parece marcada (checkbox checked
    o imagen de check dentro de esa misma celda). Se mira por celda, no por
    fila, porque las fichas ponen todas las opciones en la misma fila."""
    celdas = re.findall(r"<td[^>]*>(.*?)</td>", seccion_html, re.S | re.I)
    for c in celdas:
        if re.search(patron, c, re.I) and re.search(r"checked|selected|<img[^>]*(ok|check|tick|verde)", c, re.I):
            return True
    return False

def extraer_caracteristicas(html):
    etapas = _seccion(html, "ETAPAS EDUCATIVAS", "OPCIONES LING")
    filas = _filas(etapas)
    texto = _texto(etapas)

    tiene_eso = any("Educación Secundaria Obligatoria" in f for f in filas)
    tiene_bach = any("Bachillerato" in f for f in filas)
    tiene_fp = any(re.search(r"Formación Profesional|Grado [ABC]|Ciclo", f, re.I) for f in filas)
    vespertino = re.search(r"vespertino", texto, re.I) is not None
    nocturno = re.search(r"\bnocturno\b", texto, re.I) is not None
    bilingue = any(re.search(r"[Bb]iling", f) for f in filas)
    modalidades_bach = [f.strip() for f in filas
                        if re.search(r"Ciencias y Tecnología|Humanidades|Artes|General", f)]

    jornada = _seccion(html, r"Jornada:?", "Integración|ADSCRIPCIÓN")
    dividida = _marcado(jornada, "dividida")
    continuada = _marcado(jornada, "continuada")
    if dividida and not continuada:
        jornada_txt = "dividida (2 sesiones / turno de tarde)"
    elif continuada and not dividida:
        jornada_txt = "continuada"
    else:
        jornada_txt = "desconocida"

    return {
        "tiene_eso": tiene_eso, "tiene_bachillerato": tiene_bach, "tiene_fp": tiene_fp,
        "vespertino": vespertino, "nocturno": nocturno, "bilingue": bilingue,
        "n_modalidades_bach": len(modalidades_bach), "jornada": jornada_txt,
    }

def procesar(todos=False):
    ies = pd.read_csv(IES_CSV, dtype={"CODIGO": str})
    try:
        previo = pd.read_csv(SALIDA, dtype={"CODIGO": str}) if not todos else None
    except FileNotFoundError:
        previo = None
    hechos = set(previo["CODIGO"]) if previo is not None else set()

    sesion = _sesion()
    resultados = list(previo.to_dict("records")) if previo is not None else []
    pendientes = [c for c in ies["CODIGO"] if c not in hechos]
    print(f"Centros: {len(ies)} | ya procesados: {len(hechos)} | pendientes: {len(pendientes)}")

    errores = 0
    for n, codigo in enumerate(pendientes, 1):
        try:
            r = sesion.get(FICHA, params={"cdCentro": codigo}, timeout=60)
            r.raise_for_status()
            caracteristicas = extraer_caracteristicas(r.text)
        except Exception as e:
            print(f"  ERROR en {codigo}: {e}")
            errores += 1
            caracteristicas = {k: None for k in
                               ["tiene_eso", "tiene_bachillerato", "tiene_fp",
                                "vespertino", "nocturno", "bilingue",
                                "n_modalidades_bach", "jornada"]}
        resultados.append({"CODIGO": codigo, **caracteristicas})
        if n % 25 == 0:
            print(f"  {n}/{len(pendientes)} ...")
            pd.DataFrame(resultados).to_csv(SALIDA, index=False)
        time.sleep(ESPERA_S)

    df = pd.DataFrame(resultados)
    df.to_csv(SALIDA, index=False)
    print(f"\nGuardado {SALIDA} ({len(df)} centros, {errores} errores)")

    base = pd.read_csv(IES_CSV, dtype={"CODIGO": str})
    cols_nuevas = [c for c in df.columns if c != "CODIGO" and c not in base.columns]
    if cols_nuevas:
        base = base.merge(df[["CODIGO"] + cols_nuevas], on="CODIGO", how="left")
        base.to_csv(IES_CSV, index=False)
        print(f"Fusionado en {IES_CSV}: columnas nuevas {cols_nuevas}")

    if errores:
        print("Re-ejecuta el script para reintentar solo los fallidos (es reanudable).")

if __name__ == "__main__":
    procesar(todos="--todos" in sys.argv)
