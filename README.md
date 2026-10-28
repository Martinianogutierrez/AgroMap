# Ambientación multicapa y prescripción VRA

Prototipo para el Desafío 02 de Hackatón Agro 2026. Integra registros de cosecha,
índices satelitales y capas de vigor de dron sobre una grilla métrica de 10 m; calcula
un score de potencial, propone zonas de manejo y exporta una prescripción ESRI
Shapefile compatible con flujos de trabajo de maquinaria.

> **Validación agronómica necesaria:** las tasas de 55.000, 70.000 y 85.000 semillas/ha
> son valores iniciales de demostración, no una recomendación para aplicar sin validar
> cultivo, híbrido/variedad, ambiente, poder germinativo y criterio del asesor. Los
> resultados tampoco validan la calibración del monitor de cosecha.

## Estructura

- `src/ingestion.py`: lectura, extracción segura de ZIPs anidados, limpieza, grilla,
  IDW y muestreo de GeoTIFF.
- `src/overlay_engine.py`: normalización min-max y score multicapa.
- `src/clustering.py`: K-Means, suavizado espacial y tasas de siembra por zona.
- `src/export.py`: exportación `.shp/.shx/.dbf/.prj/.cpg` y paquete ZIP.
- `src/pipeline.py`: orquestación del proceso.
- `scripts/run_pipeline.py`: ejecución por terminal.
- `app.py`: tablero Streamlit con mapa, indicadores y descarga.
- `tests/test_pipeline.py`: prueba de integración con un lote sintético y un ZIP
  anidado con el formato de `porvenir.zip`.

## Requisitos

Python 3.10 o posterior. Instalar dependencias en un entorno virtual:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

La carpeta `data/` no se incluye en el repositorio. Copiar allí los aportes o usar
rutas locales. Para generar una prescripción:

```bash
python -m scripts.run_pipeline \
  --harvest data/porvenir.zip \
  --harvest-match cebada \
  --boundary data/lote_6.shp \
  --satellite data/ndvi.tif \
  --drone data/vigor_dron.tif \
  --zones 3 \
  --out outputs
```

El programa descubre y extrae ZIPs de cultivos anidados. Si más de un shapefile
contiene `VRYIELDMAS`, `--harvest-match` debe identificar el cultivo/campaña (por
ejemplo, `cebada` o `soja`). El límite del lote es obligatorio para no confundir la
envolvente de puntos cosechados con el polígono real del campo. Los vectores deben
tener CRS definido; los datos se reproyectan a EPSG:32721.

Los GeoTIFF monocanal se leen automáticamente. Para un raster multibanda, seleccionar
la banda de índice adecuada con `--satellite-band` y/o `--drone-band`. No usar una
banda RGB como índice de vigor sin justificarla o transformarla antes. Los pesos se
reparten por igual entre las capas disponibles por defecto; se pueden fijar
explícitamente (deben sumar 1):

```bash
python -m scripts.run_pipeline --harvest data/porvenir.zip \
  --harvest-match soja --boundary data/lote_6.shp \
  --w-satellite 0.3 --w-drone 0.3 --w-yield 0.4
```

La limpieza excluye velocidad `<= 1 km/h`, ancho de labor no positivo y rendimiento
fuera de `[0.5, 12] t/ha`; elimina puntos coincidentes exactos. Los registros de
cebada y soja no se combinan automáticamente. La guía del desafío advierte que las
fechas y la relación entre campañas/capas deben confirmarse: un IDW de rendimiento no
demuestra por sí solo comparabilidad temporal ni elimina solapes físicos entre pasadas.

## Tablero web

```bash
streamlit run app.py
```

El tablero lee rutas locales visibles para el proceso de Streamlit. Permite elegir las
capas, ponderar satélite y dron (rendimiento completa la suma a 1), mostrar zonas sobre
una imagen satelital con alternativa de calles, consultar hectáreas/semillas estimadas
y descargar el ZIP.

## Pruebas

```bash
pytest -q
```

La prueba de integración genera entradas pequeñas sintéticas, procesa una estructura
ZIP anidada como la documentada y valida el shapefile exportado. No sustituye una
ejecución con los aportes originales: `porvenir.zip`, el límite validado de Lote 6 y
los GeoTIFF no están presentes en este workspace.

## ------ PROMPT --------

# PROMPT FOR OPENCODE: MULTI-LAYER AGRICULTURAL ZONING & VARIABLE RATE PRESCRIPTION SYSTEM (VRA)

## 1. Context & Objective
We are building a production-ready software prototype for Hackatón Agro 2026 (Challenge 02: Multi-layer Zoning).
The goal is to process and integrate multi-source spatial datasets from **Lote 6 (El Porvenir field, 62 hectares)** to generate an automated **productive potential zoning map** and export a **Variable Rate Application (VRA) Seeding Prescription Shapefile (.shp)** compatible with agricultural machinery monitors (e.g., John Deere GreenStar, Case IH, Stara).

---

## 2. Dataset Specifications (Drive Folder & Input Shapefiles)
The project environment has access to the official Drive dataset files for Lote 6:
1. **Harvest Records (`porvenir.zip` & auxiliary yield ZIPs):**
   - **Barley & Soybean Vector Datasets:** Point shapefiles in **WGS 84 (EPSG:4326)**.
   - **John Deere Monitor DBF Headers:** `VRYIELDMAS` (mass yield in t/ha), `WetMass` (t/ha), `Moisture` (%), `DRYMATTER` (%), `Elevation` (m), `SWATHWIDTH` (m), `VEHICLSPEED` (km/h).
   - **Cleaning Pipeline Requirements:** Remove header/turnaround zeros, zero-speed points (`VEHICLSPEED == 0`), overlaps, and extreme outliers before interpolation.
2. **Drone Vigor Raster:**
   - GeoTIFF Orthomosaic raster (~2.4 GB) providing high-resolution canopy imagery/vegetative index.
3. **Historical Satellite NDVI & Productivity Layers:**
   - Multi-temporal Sentinel/Planet NDVI series and historical productivity shapefiles (`Productividad.zip`, `Ambientes.zip`).

---

## 3. System Architecture & Component Requirements

Implement a modular Python project structured as follows:

### Step 1: Data Ingestion & Spatial Preprocessing (`src/ingestion.py`)
- Read vector Shapefiles (`.shp` with harvest fields like `VRYIELDMAS`) and GeoTIFF rasters.
- Reproject all inputs to a metric Coordinate Reference System (e.g., **UTM Zone 21S / EPSG:32721** for Argentina/Buenos Aires region) to enable metric grid calculations ($10\text{m} \times 10\text{m}$).
- Filter harvest point anomalies:
  - Exclude rows where `VEHICLSPEED <= 1.0` km/h or `SWATHWIDTH == 0`.
  - Filter yield outliers (`VRYIELDMAS < 0.5` or `VRYIELDMAS > 12.0` t/ha).
- Interpolate cleaned harvest points onto a regular $10\text{m} \times 10\text{m}$ grid using Inverse Distance Weighting (IDW) or SciPy GridData.

### Step 2: Multi-Layer Weighted Overlay Engine (`src/overlay_engine.py`)
- Perform **Min-Max Normalization (0.0 to 1.0)** across each layer:
  $$X_{\text{norm}} = \frac{X - X_{\text{min}}}{X_{\text{max}} - X_{\text{min}}}$$
- Compute the **Composite Productive Score**:
  $$\text{Score} = (w_{\text{satellite}} \cdot \text{Sat}_{\text{norm}}) + (w_{\text{drone}} \cdot \text{Drone}_{\text{norm}}) + (w_{\text{yield}} \cdot \text{Yield}_{\text{norm}})$$
  enforcing $\sum w_i = 1.0$ ($w_1 + w_2 + w_3 = 1.0$).

### Step 3: Clustering & VRA Prescription Mapping (`src/clustering.py`)
- Classify grid cells into **3 to 5 Management Zones** using **K-Means Clustering** or **Fisher-Jenks Natural Breaks**.
- Apply spatial smoothing (polygon disaggregation & dissolving) to remove single-pixel "salt-and-pepper" noise for smooth machinery pass lines.
- Assign target agronomic seeding rates per zone (e.g., Low Potential: 55,000 seeds/ha; Medium: 70,000 seeds/ha; High: 85,000 seeds/ha).

### Step 4: Universal Machinery Shapefile Exporter (`src/export.py`)
- Package and export the resulting vector polygon layer as an ESRI Shapefile (`.shp`, `.shx`, `.dbf`, `.prj`) with standard DBF attributes:
  - `ZONE_ID` (Integer: 1, 2, 3)
  - `ZONE_NAME` (String: "High Potential", "Medium", "Low")
  - `SEED_RATE` (Numeric: seeds/ha)
  - `AVG_YIELD` (Numeric: t/ha)

### Step 5: Interactive Web Dashboard (`app.py`)
- Build a **Streamlit** app featuring:
  - Dynamic slider controls for layer weights ($w_1, w_2, w_3$).
  - Folium/Leaflet interactive map displaying the resulting management zones over satellite basemaps.
  - Summary metrics table (surface breakdown in hectares and total seed requirements).
  - Download button for the output `.zip` package containing the prescription shapefile.

---

## 4. Expected Deliverables
1. **Clean Project Structure** (`ingestion.py`, `overlay_engine.py`, `clustering.py`, `export.py`, `app.py`).
2. **Production Code** utilizing `geopandas`, `shapely`, `rasterio`, `scikit-learn`, `scipy`, and `streamlit`.
3. **Execution Script & Test Driver** that reads the actual dataset structure from `porvenir.zip` and verifies end-to-end shapefile generation.
