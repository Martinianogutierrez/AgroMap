"""Streamlit interface for interactive productive-potential zoning and VRA export."""

from __future__ import annotations

from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from src.pipeline import run_pipeline

st.set_page_config(page_title="Lote 6 | Ambientación multicapa", layout="wide")
st.title("Ambientación multicapa y prescripción VRA")
st.caption(
    "Prototipo para revisión agronómica. Los mapas, campañas y tasas sugeridas deben "
    "validarse antes de aplicarse en maquinaria."
)

with st.sidebar:
    st.header("Datos de entrada")
    harvest_path = st.text_input("Cosecha (.zip, .shp o carpeta)", "data/porvenir.zip")
    harvest_match = st.text_input("Cultivo/campaña dentro del ZIP", "cebada")
    boundary_path = st.text_input("Límite del lote (vector)", "data/lote_6.shp")
    satellite_path = st.text_input("NDVI/productividad (GeoTIFF, opcional)", "")
    drone_path = st.text_input("Vigor de dron (GeoTIFF, opcional)", "")
    satellite_band = st.number_input("Banda satelital", min_value=1, value=1, step=1)
    drone_band = st.number_input("Banda de dron", min_value=1, value=1, step=1)
    zones_count = st.selectbox("Cantidad de ambientes", [3, 4, 5], index=0)

    st.header("Pesos del score")
    satellite_weight = (
        st.slider("Satélite", 0.0, 1.0, 0.0, 0.05) if satellite_path.strip() else 0.0
    )
    max_drone_weight = 1.0 - satellite_weight
    drone_weight = (
        st.slider("Dron", 0.0, max_drone_weight, 0.0, 0.05)
        if drone_path.strip() and max_drone_weight > 0
        else 0.0
    )
    yield_weight = max(0.0, 1.0 - satellite_weight - drone_weight)
    st.metric("Rendimiento", f"{yield_weight:.2f}")
    st.caption("El peso de rendimiento completa automáticamente la suma a 1.00.")
    st.caption("Usar una banda de índice de vigor; una banda RGB cruda no es un índice.")
    run_clicked = st.button("Generar ambientación", type="primary", use_container_width=True)

if run_clicked:
    required_paths = [Path(harvest_path), Path(boundary_path)]
    optional_paths = [
        Path(path) for path in (satellite_path, drone_path) if path.strip()
    ]
    missing = [str(path) for path in required_paths + optional_paths if not path.exists()]
    if missing:
        st.error(f"No se encontraron estas rutas locales: {', '.join(missing)}")
    else:
        weights = {
            "satellite": satellite_weight,
            "drone": drone_weight,
            "yield": yield_weight,
        }
        with st.spinner("Procesando capas y generando la prescripción..."):
            try:
                zones, archive_path = run_pipeline(
                    harvest_path=harvest_path,
                    boundary_path=boundary_path,
                    output_dir="outputs",
                    harvest_match=harvest_match.strip() or None,
                    satellite_path=satellite_path.strip() or None,
                    drone_path=drone_path.strip() or None,
                    satellite_band=int(satellite_band) if satellite_path.strip() else None,
                    drone_band=int(drone_band) if drone_path.strip() else None,
                    weights=weights,
                    n_zones=int(zones_count),
                )
            except (FileNotFoundError, ValueError, OSError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.session_state["zones"] = zones
                st.session_state["archive_path"] = str(archive_path)

if "zones" in st.session_state:
    zones = st.session_state["zones"]
    st.subheader("Mapa de ambientes")
    map_data = zones.to_crs("EPSG:4326")
    map_center = map_data.geometry.union_all().centroid
    map_view = folium.Map(
        location=[map_center.y, map_center.x], zoom_start=15, tiles=None
    )
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Tiles © Esri",
        name="Imagen satelital",
        max_zoom=19,
    ).add_to(map_view)
    folium.TileLayer("OpenStreetMap", name="Calles").add_to(map_view)
    zone_colors = {
        "Low Potential": "#d73027",
        "Medium Potential": "#fee08b",
        "High Potential": "#1a9850",
        "Very Low": "#a50026",
        "Low": "#f46d43",
        "Medium": "#fee08b",
        "High": "#66bd63",
        "Very High": "#006837",
        "Medium-Low": "#fdae61",
        "Medium-High": "#a6d96a",
    }

    def style_zone(feature: dict) -> dict:
        name = feature["properties"].get("ZONE_NAME", "")
        return {
            "fillColor": zone_colors.get(name, "#3288bd"),
            "color": "#333333",
            "weight": 1,
            "fillOpacity": 0.55,
        }

    folium.GeoJson(
        map_data,
        name="Management zones",
        style_function=style_zone,
        tooltip=folium.GeoJsonTooltip(
            fields=["ZONE_NAME", "SEED_RATE", "AVG_YIELD"],
            aliases=["Zone", "Seeds/ha", "Average yield (t/ha)"],
        ),
    ).add_to(map_view)
    folium.LayerControl().add_to(map_view)
    bounds = map_data.total_bounds
    map_view.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
    st_folium(map_view, use_container_width=True, height=550)

    metrics = zones.to_crs("EPSG:32721").copy()
    metrics["AREA_HA"] = metrics.geometry.area / 10_000
    metrics["SEEDS_TOTAL"] = metrics["AREA_HA"] * metrics["SEED_RATE"]
    summary = (
        metrics.groupby(["ZONE_ID", "ZONE_NAME", "SEED_RATE"], as_index=False)
        .agg(AREA_HA=("AREA_HA", "sum"), SEEDS_TOTAL=("SEEDS_TOTAL", "sum"), AVG_YIELD=("AVG_YIELD", "mean"))
        .sort_values("ZONE_ID")
    )
    left, right = st.columns(2)
    left.metric("Superficie zonificada", f"{summary['AREA_HA'].sum():.2f} ha")
    right.metric("Semillas estimadas", f"{summary['SEEDS_TOTAL'].sum():,.0f}")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    archive_path = Path(st.session_state["archive_path"])
    st.download_button(
        "Descargar prescripción Shapefile (.zip)",
        data=archive_path.read_bytes(),
        file_name=archive_path.name,
        mime="application/zip",
    )
