#!/usr/bin/env bash
# descarga_datos.sh: prepara el entorno Python y baja TODOS los datos del
# proyecto (IES, 5 GTFS, callejero OSM). Reanudable: salta lo que ya existe.
# Ejecutar: bash descarga_datos.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export PATH="$HOME/.local/bin:$PATH"

paso() { echo; echo "==> $1"; }

paso "1/4 uv y dependencias Python"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12
if [ -f pyproject.toml ]; then
    uv sync
else
    uv add requests pandas pyproj streamlit streamlit-folium plotly openpyxl
fi

paso "2/4 Catalogo de IES publicos"
[ -f ies_publicos_madrid.csv ] || uv run descargar_ies_publicos.py
echo "   ies_publicos_madrid.csv: $(du -h ies_publicos_madrid.csv | cut -f1)"

paso "3/4 Feeds GTFS"
FALTAN=0
for f in gtfs_metro.zip gtfs_emt.zip gtfs_interurbanos.zip gtfs_metro_ligero.zip gtfs_cercanias.zip; do
    [ -f "$f" ] || { echo "   falta $f"; FALTAN=1; }
done
if [ "$FALTAN" = "1" ]; then
    for k in metro emt interurbanos ligero cercanias; do
        uv run descargar_transportes.py "$k" || true
    done
fi

paso "4/4 Callejero OSM (~86 MB)"
# solo si falta o pesa poco (una pagina de error guardada como pbf pesa bytes)
if [ ! -s madrid-latest.osm.pbf ] || [ "$(stat -c%s madrid-latest.osm.pbf)" -lt 50000000 ]; then
    # ruta nueva (post 19.11.2026) y luego la vieja
    curl -fL -o madrid-latest.osm.pbf \
        "https://download.geofabrik.de/europe/spain/madrid/madrid-latest.osm.pbf" \
    || curl -fL -o madrid-latest.osm.pbf \
        "https://download.geofabrik.de/europe/spain/madrid-latest.osm.pbf"
fi
echo "   madrid-latest.osm.pbf: $(du -h madrid-latest.osm.pbf | cut -f1)"

echo
echo "Datos listos:"
ls -lh ies_publicos_madrid.csv gtfs_*.zip madrid-latest.osm.pbf
echo
echo "Siguiente paso: bash arrancar.sh [puerto]"
