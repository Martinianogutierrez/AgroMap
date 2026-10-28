"""ESRI Shapefile and ZIP packaging for machinery VRA prescriptions."""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd

REQUIRED_FIELDS = ("ZONE_ID", "ZONE_NAME", "SEED_RATE", "AVG_YIELD")


def export_prescription(
    zones: gpd.GeoDataFrame,
    output_dir: str | Path,
    basename: str = "vra_prescription",
) -> Path:
    """Write the prescribed zone attributes as a shapefile and return its ZIP."""
    if zones.empty:
        raise ValueError("Cannot export an empty prescription")
    if zones.crs is None:
        raise ValueError("Prescription zones must have a defined CRS")
    missing = [field for field in REQUIRED_FIELDS if field not in zones.columns]
    if missing:
        raise ValueError(f"Prescription zones are missing fields: {', '.join(missing)}")
    if not basename or Path(basename).name != basename:
        raise ValueError("Output basename must be a non-empty filename without directories")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    shapefile_path = destination / f"{basename}.shp"
    fields = zones[list(REQUIRED_FIELDS) + [zones.geometry.name]].copy()
    fields["ZONE_ID"] = fields["ZONE_ID"].astype("int32")
    fields["ZONE_NAME"] = fields["ZONE_NAME"].astype(str)
    fields["SEED_RATE"] = fields["SEED_RATE"].astype("int32")
    fields["AVG_YIELD"] = fields["AVG_YIELD"].astype(float)
    fields.to_file(shapefile_path, driver="ESRI Shapefile", encoding="UTF-8", index=False)

    required_components = (".shp", ".shx", ".dbf", ".prj")
    missing_components = [
        extension for extension in required_components if not (destination / f"{basename}{extension}").is_file()
    ]
    if missing_components:
        raise RuntimeError(f"Shapefile driver did not create components: {missing_components}")

    archive_path = destination / f"{basename}.zip"
    components = [
        destination / f"{basename}{extension}"
        for extension in (*required_components, ".cpg")
        if (destination / f"{basename}{extension}").is_file()
    ]
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for component in components:
            archive.write(component, arcname=component.name)
    return archive_path
