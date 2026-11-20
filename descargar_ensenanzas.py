"""
Paso 1b (v2): enriquecer los IES con ensenanzas y turnos desde el Buscador de
centros de la Comunidad de Madrid (Servicio Web a Padres de Alumnos).
v2:
  - Las secciones se anclan en la ULTIMA aparicion del titulo (la pagina tiene
    un menu superior con los mismos textos, que antes secuestraba la extraccion)
  - Repara estados corruptos: reintenta filas fallidas (a None) y columnas
    fusionadas vacias en ies_publicos_madrid.csv
  - Los errores se loguean con numero de linea exacto

Fuente: https://gestiona.comunidad.madrid/wpad_pub/run/j/MostrarFichaCentro.icm
Uso: uv run descargar_ensenanzas.py          (reanudable, reintenta los fallidos)
     uv run descargar_ensenanzas.py --todos
"""
import re
import sys
import time
import traceback
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

def _tablas(html):
    return re.findall(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)

def _ultima_tabla_con(html, patron):
    """La ultima <table> de la pagina cuyo contenido contiene 'patron'. Anclar
    por tabla (no por posicion de texto) evita que el menu superior o los
    propios textos de opciones secuestren la extraccion."""
    candidatas = [t for t in _tablas(html) if re.search(patron, t, re.I)]
    return candidatas[-1] if candidatas else ""

_CHECK = re.compile(r"checked|selected|<img[^>]*(ok|check|tick|verde)", re.I)

def _marcado(seccion_html, patron):
    """True si la opcion 'patron' parece marcada: la marca esta en su propia
    celda, o en la celda INMEDIATAMENTE ANTERIOR y esa celda no contiene nada
    mas que el checkbox. Asi no se 'sangra' una marca a la opcion vecina."""
    celdas = re.findall(r"<td[^>]*>(.*?)</td>", seccion_html, re.S | re.I)
    for i, c in enumerate(celdas):
        if re.search(patron, c, re.I):
            if _CHECK.search(c):
                return True
            if i > 0 and _CHECK.search(celdas[i - 1]):
                resto = re.sub(r"<[^>]+>", "", celdas[i - 1]).replace("&nbsp;", " ").strip()
                if not resto:
                    return True
    return False

def extraer_caracteristicas(html):
    if not html:
        raise ValueError("la ficha llego vacia (posible pagina de sesion/error)")
    etapas = _ultima_tabla_con(html, "ETAPAS EDUCATIVAS")
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

    jornada_html = _ultima_tabla_con(html, "dividida")
    dividida = _marcado(jornada_html, "dividida")
    continuada = _marcado(jornada_html, "continuada")
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
    if previo is not None:
        # reparacion: las filas fallidas (tiene_eso a NaN) cuentan como pendientes
        fallidas = previo["tiene_eso"].isna().sum()
        if fallidas:
            print(f"Reparando {fallidas} filas fallidas de un run anterior")
        previo = previo.dropna(subset=["tiene_eso"])
    hechos = set(previo["CODIGO"]) if previo is not None else set()

    sesion = _sesion()
    resultados = list(previo.to_dict("records")) if previo is not None else []
    pendientes = [c for c in ies["CODIGO"] if c not in hechos]
    print(f"Centros: {len(ies)} | ya procesados: {len(hechos)} | pendientes: {len(pendientes)}")

    errores = 0
    for n, codigo in enumerate(pendientes, 1):
        try:
            r = sesion.get(FICHA, params={"cdCentro": str(codigo)}, timeout=60)
            r.raise_for_status()
            caracteristicas = extraer_caracteristicas(r.text)
        except Exception as e:
            linea = traceback.extract_tb(e.__traceback__)[-1].lineno
            print(f"  ERROR en {codigo}: {type(e).__name__}: {e} (linea {linea})")
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
    cols_carac = [c for c in df.columns if c != "CODIGO"]
    for c in cols_carac:
        # reparacion: columna existente pero toda vacia (run fallido anterior) -> borrar y re-fusionar
        if c in base.columns and base[c].isna().all():
            base = base.drop(columns=[c])
    cols_nuevas = [c for c in cols_carac if c not in base.columns]
    if cols_nuevas:
        base = base.merge(df[["CODIGO"] + cols_nuevas], on="CODIGO", how="left")
        base.to_csv(IES_CSV, index=False)
        print(f"Fusionado en {IES_CSV}: columnas nuevas {cols_nuevas}")

    if errores:
        print("Re-ejecuta el script para reintentar solo los fallidos (es reanudable).")

if __name__ == "__main__":
    procesar(todos="--todos" in sys.argv)
