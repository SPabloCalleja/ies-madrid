#!/usr/bin/env bash
# Paso 2a-bis: construir el grafo OTP2 y arrancar el servidor (Docker).
# Corregido: la imagen oficial monta /var/opentripplanner como directorio base
# y NO hay que pasarle ningun directorio por linea de comandos.
# Requisitos: Docker y ~6 GB de RAM libres para construir.

set -euo pipefail

mkdir -p otp/data
cp gtfs_metro.zip gtfs_emt.zip gtfs_interurbanos.zip \
   gtfs_metro_ligero.zip gtfs_cercanias.zip madrid-latest.osm.pbf otp/data/

# Config de construccion
cat > otp/data/build-config.json <<'EOF'
{
  "maxTransferWalkDistance": 1500,
  "transit": true
}
EOF

# 1) Construir el grafo y guardarlo como otp/data/graph.obj (tarda 5-10 min y SALE)
docker run -it --rm \
  -e JAVA_TOOL_OPTIONS="-Xmx6g" \
  -v "$PWD/otp/data:/var/opentripplanner" \
  opentripplanner/opentripplanner:latest --build --save

# 2) Cargar el grafo ya construido y servir (queda corriendo; Ctrl+C para parar)
docker run -it --rm -p 8080:8080 \
  -e JAVA_TOOL_OPTIONS="-Xmx4g" \
  -v "$PWD/otp/data:/var/opentripplanner" \
  opentripplanner/opentripplanner:latest --load --serve

# Cuando arranque, abre http://localhost:8080 para verificar rutas a mano,
# y en otro terminal ejecuta: uv run matriz_tiempos_otp.py
