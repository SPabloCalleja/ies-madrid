#!/usr/bin/env bash
# Despliegue del proyecto IES-Madrid en un servidor Ubuntu.
# Uso: bash deploy.sh [puerto_app]     (por defecto 8501)
set -euo pipefail

PUERTO_APP="${1:-8501}"
PUERTO_OTP="8080"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "==> 1/7 Dependencias del sistema (docker, curl)"
if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io curl
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true

echo "==> 2/7 uv y entorno Python"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12
uv add requests pandas pyproj streamlit streamlit-folium plotly openpyxl

echo "==> 3/7 Descarga de datos (~150 MB: IES, 5 GTFS, callejero OSM)"
[ -f ies_publicos_madrid.csv ] || uv run descargar_ies_publicos.py
uv run descargar_transportes.py

echo "==> 4/7 Construccion del grafo OTP (5-10 min, ~6 GB de RAM)"
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

echo "==> 5/7 Servidor OTP en el puerto $PUERTO_OTP (detached, con restart)"
sudo docker rm -f otp >/dev/null 2>&1 || true
sudo docker run -d --name otp --restart unless-stopped \
  -p "$PUERTO_OTP:8080" \
  -e JAVA_TOOL_OPTIONS="-Xmx4g" \
  -v "$DIR/otp/data:/var/opentripplanner" \
  opentripplanner/opentripplanner:latest --load --serve

echo -n "==> 6/7 Esperando a que OTP cargue el grafo"
until curl -s -o /dev/null "http://localhost:$PUERTO_OTP/"; do
    echo -n "."; sleep 2
done
echo " OK"

echo "==> 7/7 Lanzando la app en el puerto $PUERTO_APP"
echo "    La app estara en: http://<ip-de-este-servidor>:$PUERTO_APP"
echo "    (si usas ufw: sudo ufw allow $PUERTO_APP/tcp)"
exec uv run streamlit run app_ies.py \
    --server.port "$PUERTO_APP" \
    --server.address 0.0.0.0 \
    --browser.gatherUsageStats false
