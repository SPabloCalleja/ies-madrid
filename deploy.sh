#!/usr/bin/env bash
# Despliegue del proyecto IES-Madrid en un servidor Ubuntu (v2, reanudable).
# Uso: bash deploy.sh [puerto_app]     (por defecto 8501)
# - Salta cualquier paso cuyo artefacto ya exista (datos, grafo, CSV de tiempos)
# - Incluye el paso de la matriz de tiempos que faltaba en la v1
set -euo pipefail

PUERTO_APP="${1:-8501}"
PUERTO_OTP="8080"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

export PATH="$HOME/.local/bin:$PATH"

paso() { echo; echo "==> $1"; }

paso "1/9 Docker"
if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io curl
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true

paso "2/9 uv y dependencias Python"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12
if [ -f pyproject.toml ]; then
    uv sync
else
    uv add requests pandas pyproj streamlit streamlit-folium plotly openpyxl
fi

paso "3/9 Catalogo de IES publicos"
[ -f ies_publicos_madrid.csv ] || uv run descargar_ies_publicos.py

paso "4/9 Feeds GTFS"
FALTAN=0
for f in gtfs_metro.zip gtfs_emt.zip gtfs_interurbanos.zip gtfs_metro_ligero.zip gtfs_cercanias.zip; do
    [ -f "$f" ] || { echo "falta $f"; FALTAN=1; }
done
if [ "$FALTAN" = "1" ]; then
    # feeds uno a uno: si falla el OSM (Geofabrik en transicion de rutas), no
    # tira abajo lo que ya esta
    for k in metro emt interurbanos ligero cercanias; do
        uv run descargar_transportes.py "$k" || true
    done
fi

paso "5/9 Callejero OSM (~86 MB)"
# solo si falta o pesa poco (una pagina de error guardada como pbf pesa bytes)
if [ ! -s madrid-latest.osm.pbf ] || [ "$(stat -c%s madrid-latest.osm.pbf)" -lt 50000000 ]; then
    # ruta nueva (post 19.11.2026) y luego la vieja
    curl -fL -o madrid-latest.osm.pbf \
        "https://download.geofabrik.de/europe/spain/madrid/madrid-latest.osm.pbf" \
    || curl -fL -o madrid-latest.osm.pbf \
        "https://download.geofabrik.de/europe/spain/madrid-latest.osm.pbf"
fi
echo "madrid-latest.osm.pbf: $(du -h madrid-latest.osm.pbf | cut -f1)"

paso "6/9 Matriz de tiempos (si no existe ya)"
if [ ! -f ies_por_tiempo_otp.csv ]; then
    # necesita OTP arriba, asi que se hace tras arrancarlo (paso 8)
    echo "   (se calculara despues de arrancar OTP)"
fi

paso "7/9 Construccion del grafo OTP (5-10 min, ~6 GB RAM)"
if [ ! -f otp/data/graph.obj ]; then
    mkdir -p otp/data
    cp gtfs_metro.zip gtfs_emt.zip gtfs_interurbanos.zip gtfs_metro_ligero.zip \
       gtfs_cercanias.zip madrid-latest.osm.pbf otp/data/
    cat > otp/data/build-config.json <<'EOF'
{
  "maxTransferWalkDistance": 1500,
  "transit": true
}
EOF
    sudo docker run --rm \
      -e JAVA_TOOL_OPTIONS="-Xmx6g" \
      -v "$DIR/otp/data:/var/opentripplanner" \
      opentripplanner/opentripplanner:latest --build --save
else
    echo "   grafo ya construido, se reutiliza"
fi

paso "8/9 Servidor OTP en el puerto $PUERTO_OTP"
sudo docker rm -f otp >/dev/null 2>&1 || true
sudo docker run -d --name otp --restart unless-stopped \
  -p "$PUERTO_OTP:8080" \
  -e JAVA_TOOL_OPTIONS="-Xmx4g" \
  -v "$DIR/otp/data:/var/opentripplanner" \
  opentripplanner/opentripplanner:latest --load --serve

echo -n "   esperando a que cargue el grafo"
until curl -s -o /dev/null "http://localhost:$PUERTO_OTP/"; do
    echo -n "."; sleep 2
done
echo " OK"

paso "9/9 Matriz de tiempos + app en el puerto $PUERTO_APP"
if [ ! -f ies_por_tiempo_otp.csv ]; then
    uv run matriz_tiempos_otp.py
fi

echo
echo "La app quedara en: http://<ip-de-este-servidor>:$PUERTO_APP"
echo "(si usas ufw: sudo ufw allow $PUERTO_APP/tcp)"
exec uv run streamlit run app_ies.py \
    --server.port "$PUERTO_APP" \
    --server.address 0.0.0.0 \
    --browser.gatherUsageStats false
