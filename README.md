# IES Madrid — acceso en transporte público

Miniproyecto para ordenar los **institutos públicos de la Comunidad de Madrid**
por tiempo real de viaje en transporte público (metro, autobús urbano e
interurbano, Metro Ligero y Cercanías + a pie) desde una dirección de origen,
**llegando a una hora concreta** (p. ej. 08:30).

No ordena por distancia geográfica sino por **tiempo puerta a puerta** según los
horarios oficiales (GTFS), con desglose por modo de transporte, esperas y hora
de salida de casa. Pensado para decidir una solicitud de destinos, pero vale
para cualquier estudio de accesibilidad en Madrid.

## Cómo funciona

    CSV centros educativos      GTFS (Metro, EMT, interurbanos,       Callejero OSM
           |                     Metro Ligero, Cercanías)              de Geofabrik
           |                            |                                    |
           v                            v                                    v
    descargar_ies_publicos.py    descargar_transportes.py (valida zips)    |
           |                            |                                    |
           |                            +------------------+-----------------+
           |                                               v
           |                              OpenTripPlanner 2 (Docker): --build --save
           |                                               |
           v                                               v
    matriz_tiempos_otp.py  --- GraphQL /otp/gtfs/v1 ---> OTP en el puerto 8080
           |
           v
    ies_por_tiempo_otp.csv  (minutos, salida, transbordos, desglose por modo)
           |
           v
    app_ies.py (Streamlit): mapa interactivo, clic para ver trayectos,
    desglose por modo, GPX por trayecto y exportación a Excel

## Fuentes de datos (todas abiertas)

| Dato | Fuente |
|---|---|
| Centros educativos | Portal de datos abiertos de la Comunidad de Madrid |
| GTFS Metro, Interurbanos, Metro Ligero, Cercanías | Portal de datos abiertos del CRTM |
| GTFS EMT | Productor oficial EMT / CRTM |
| Callejero peatonal | Geofabrik (OpenStreetMap) |

## Requisitos

- Ubuntu 22.04+ (o similar)
- **8 GB de RAM** (la construcción del grafo OTP usa ~6 GB)
- ~10 GB de disco
- Acceso a internet para descargar datos

## Despliegue rápido

    git clone https://github.com/USUARIO/ies-madrid.git
    cd ies-madrid
    bash deploy.sh 8501        # el puerto que quieras para la app

El script instala Docker y uv, baja las dependencias Python, descarga los
datos, construye el grafo OTP, arranca el servidor OTP (puerto 8080) y lanza
la app de Streamlit en el puerto indicado. Al terminar, la app está en
`http://IP_DEL_SERVIDOR:8501`.

Si hay firewall: `sudo ufw allow 8501/tcp` (y `8080` si quieres exponer OTP,
aunque no es necesario: solo la app habla con él).

## Uso paso a paso (equivalente manual)

    uv run descargar_ies_publicos.py     # IES públicos en activo -> CSV
    uv run descargar_transportes.py      # 5 GTFS + OSM (valida y aplana zips)
    bash arrancar_otp.sh                 # construye grafo y sirve OTP (Docker)
    uv run matriz_tiempos_otp.py         # matriz de tiempos -> CSV final
    uv run streamlit run app_ies.py --server.port 8501 --server.address 0.0.0.0

## Mantener los datos frescos

Los GTFS **caducan** (el de Cercanías tiene validez rodante de ~4 semanas).
Antes de una decisión importante, re-ejecutar:

    uv run descargar_transportes.py && uv run matriz_tiempos_otp.py

y comprobar en la salida los rangos de validez de cada feed. En el script de
la matriz, `FECHA` debe caer dentro de todos los rangos.

Ejemplo de cron semional (domingos a las 4:00):

    0 4 * * 0 cd /opt/ies-madrid && $HOME/.local/bin/uv run descargar_transportes.py >> actualiza.log 2>&1

## Configuración

Todas las constantes están en la cabecera de cada script: dirección de origen
(`DIRECCION_CASA`), fecha y hora de llegada (`FECHA`, `HORA_LLEGADA`), máximo
de centros a conservar (`MAX_TOP`, en `matriz_tiempos_otp.py`) y puertos.

## Notas y limitaciones

- Es planificación sobre horarios oficiales (GTFS), **sin tiempo real**: es
  la métrica correcta para decisiones de vivienda/destino, pero no predice
  retrasos de un día concreto.
- El bus urbano (EMT) y el interurbano se distinguen por la agencia del
  tramo; si un operador nuevo se clasifica raro, se ve en el tooltip del tramo.
- La primera petición de la app geocodifica la vivienda con Nominatim (gratis,
  va con caché); comprobar que la dirección resuelta es la correcta.

## Estructura

    ├── deploy.sh                  despliegue completo en Ubuntu
    ├── descargar_ies_publicos.py  paso 1: catálogo de IES públicos
    ├── descargar_transportes.py   paso 2: GTFS + OSM (con validación)
    ├── arrancar_otp.sh            paso 3: grafo + servidor OTP (Docker)
    ├── matriz_tiempos_otp.py      paso 4: matriz de tiempos con desglose
    ├── app_ies.py                 paso 5: visualizador Streamlit
    └── README.md
