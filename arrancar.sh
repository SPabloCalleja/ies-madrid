#!/usr/bin/env bash
# arrancar.sh: construye el grafo (si hace falta), arranca OTP (siempre),
# calcula la matriz de tiempos (si falta) y lanza la app de Streamlit.
# Reanudable: reutiliza grafo y CSV si ya existen.
# Ejecutar: bash arrancar.sh [puerto_app]     (por defecto 8501)
# Requiere haber ejecutado antes: bash descarga_datos.sh

set -euo pipefail

PUERTO_APP="${1:-8501}"
PUERTO_OTP="8080"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export PATH="$HOME/.local/bin:$PATH"

paso() { echo; echo "==> $1"; }

paso "0/4 Comprobaciones previas"
command -v uv >/dev/null 2>&1 || { echo "FALTA uv: ejecuta antes bash descarga_datos.sh"; exit 1; }
for f in ies_publicos_madrid.csv gtfs_metro.zip gtfs_emt.zip gtfs_interurbanos.zip \
         gtfs_metro_ligero.zip gtfs_cercanias.zip madrid-latest.osm.pbf; do
    [ -f "$f" ] || { echo "FALTA $f: ejecuta antes bash descarga_datos.sh"; exit 1; }
done
if [ "$(stat -c%s madrid-latest.osm.pbf)" -lt 50000000 ]; then
    echo "madrid-latest.osm.pbf pesa $(du -h madrid-latest.osm.pbf | cut -f1): es una"
    echo "pagina de error, no el pbf. Borralo y re-ejecuta bash descarga_datos.sh"
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io
fi
sudo systemctl enable --now docker >/dev/null 2>&1 || true

paso "1/4 Construccion del grafo OTP (5-10 min, ~6 GB RAM)"
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

paso "2/4 Servidor OTP en el puerto $PUERTO_OTP"
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

paso "3/4 Matriz de tiempos (si no existe ya)"
if [ ! -f ies_por_tiempo_otp.csv ]; then
    uv run matriz_tiempos_otp.py
else
    echo "   ies_por_tiempo_otp.csv ya existe, se reutiliza"
    echo "   (para recalcularlo: rm ies_por_tiempo_otp.csv y relanza)"
fi

paso "4/4 App de Streamlit en el puerto $PUERTO_APP"
echo "La app quedara en: http://<ip-de-este-servidor>:$PUERTO_APP"
echo "(si usas ufw: sudo ufw allow $PUERTO_APP/tcp)"
exec uv run streamlit run app_ies.py \
    --server.port "$PUERTO_APP" \
    --server.address 0.0.0.0 \
    --browser.gatherUsageStats false
