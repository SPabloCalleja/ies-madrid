"""
Paso 1b (v6): enriquecer los IES con ensenanzas y turnos desde el Buscador de
centros de la Comunidad de Madrid (Servicio Web a Padres de Alumnos).
v6: extraccion por FILAS con filtros de longitud. Las paginas .icm usan tablas
    anidadas; el enfoque anterior (ultima tabla que contiene X) se tragaba
    fragmentos gigantes con otras secciones y daba "todos con Bach+FP".
    La jornada ahora se busca en toda la pagina (las etiquetas 'dividida'/
    'continuada' solo aparecen en esa tabla), con celdas adyacentes.

Fuente: https://gestiona.comunidad.madrid/wpad_pub/run/j/MostrarFichaCentro.icm
Uso: uv run descargar_ensenanzas.py --todos   (rehace los 344)
     uv run descargar_ensenanzas.py          (solo los pendientes/fallidos)
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
# cabeceras de secciones posteriores: donde termina la tabla de etapas
_FIN_ETAPAS = r"OPCIONES LING|SERVICIOS|ADSCRIPCI|INTEGRACI|TITULACI|EXCELENCIA|INNOVACI|MODALIDAD|ESTADIS"

def _sesion():
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "Mozilla/5.0 (mini-proyecto-ies-madrid/1.0)"
    return s

def _texto(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

def _texto_filas(html):
    return [_texto(f) for f in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)]

_CHECK = re.compile(r"checked|selected|<img[^>]*(ok|check|tick|verde)", re.I)

def _marcado(html, patron):
    """True si la opcion 'patron' parece marcada: la marca esta en su propia
    celda, o en la celda INMEDIATAMENTE ANTERIOR y esa celda no contiene nada
    mas que el checkbox. Asi no se 'sangra' una marca a la opcion vecina."""
    celdas = re.findall(r"<td[^>]*>(.*?)</td>", html, re.S | re.I)
    for i, c in enumerate(celdas):
        if re.search(patron, c, re.I):
            if _CHECK.search(c):
                return True
            if i > 0 and _CHECK.search(celdas[i - 1]):
                resto = re.sub(r"<[^>]+>", "", celdas[i - 1]).replace("&nbsp;", " ").strip()
                if not resto:
                    return True
    return False

def _filas_etapas(html):
    """Filas de la seccion ETAPAS EDUCATIVAS. Solo cuentan filas CORTAS (las
    filas-envoltorio de tablas anidadas contienen media pagina y quedan
    excluidas por longitud). La cabecera es la ultima fila corta que contiene
    el titulo (los menus superiores tambien lo contienen)."""
    textos = _texto_filas(html)
    cabeceras = [i for i, t in enumerate(textos)
                 if "ETAPAS EDUCATIVAS" in t.upper() and len(t) < 160]
    if not cabeceras:
        return []
    i0 = cabeceras[-1]
    fines = [j for j in range(i0 + 1, len(textos))
             if len(textos[j]) < 160 and re.search(_FIN_ETAPAS, textos[j].upper())]
    i1 = fines[0] if fines else len(textos)
    return [t for t in textos[i0:i1] if len(t) < 250]

def extraer_caracteristicas(html):
    if not html:
        raise ValueError("la ficha llego vacia (posible pagina de sesion/error)")
    filas = _filas_etapas(html)

    tiene_eso = any("Educación Secundaria Obligatoria" in t for t in filas)
    tiene_bach = any(re.search(r"Bachillerato", t) for t in filas)
    tiene_fp = any(re.search(r"Formación Profesional|Ciclo|CFGM|CFGS|Grado [ABCD]", t, re.I) for t in filas)
    vespertino = any(re.search(r"vespertino", t, re.I) for t in filas)
    nocturno = any(re.search(r"\bnocturno\b", t, re.I) for t in filas)
    bilingue = any(re.search(r"[Bb]iling", t) for t in filas)
    modalidades_bach = [t for t in filas
                        if re.search(r"Ciencias y Tecnología|Humanidades|Artes|General", t)]

    # las etiquetas de jornada solo aparecen en esa tabla: busqueda global
    dividida = _marcado(html, r"dividida")
    continuada = _marcado(html, r"continuada")
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
