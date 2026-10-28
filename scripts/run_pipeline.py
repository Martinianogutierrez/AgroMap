#!/usr/bin/env python3
"""Generate a VRA prescription from Lote 6 or another compatible field dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build 10 m productive-potential zones and export an ESRI Shapefile ZIP. "
            "Harvest ZIPs may contain nested crop ZIP files."
        )
    )
    parser.add_argument("--harvest", required=True, help=".shp, directory, or porvenir.zip input")
    parser.add_argument("--boundary", required=True, help="Field boundary vector file")
    parser.add_argument("--out", default="outputs", help="Output directory (default: outputs)")
    parser.add_argument(
        "--harvest-match",
        help="Case-insensitive crop/campaign substring, e.g. 'cebada' or 'soja'",
    )
    parser.add_argument("--satellite", help="Single-band NDVI or productivity GeoTIFF")
    parser.add_argument("--drone", help="Single-band drone vigor/index GeoTIFF")
    parser.add_argument("--satellite-band", type=int, help="Satellite raster band (defaults to 1 if single-band)")
    parser.add_argument("--drone-band", type=int, help="Drone raster band (defaults to 1 if single-band)")
    parser.add_argument("--zones", type=int, choices=range(3, 6), default=3)
    parser.add_argument("--resolution", type=float, default=10.0, help="Grid resolution in metres")
    parser.add_argument("--max-idw-distance", type=float, default=100.0, help="IDW search radius in metres")
    parser.add_argument("--w-satellite", type=float)
    parser.add_argument("--w-drone", type=float)
    parser.add_argument("--w-yield", type=float)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    provided_weights = (args.w_satellite, args.w_drone, args.w_yield)
    if any(value is not None for value in provided_weights) and not all(
        value is not None for value in provided_weights
    ):
        raise SystemExit("Provide all three weights together: --w-satellite, --w-drone, --w-yield")
    weights = None
    if all(value is not None for value in provided_weights):
        weights = {
            "satellite": float(args.w_satellite),
            "drone": float(args.w_drone),
            "yield": float(args.w_yield),
        }

    zones, archive = run_pipeline(
        harvest_path=args.harvest,
        boundary_path=args.boundary,
        output_dir=args.out,
        harvest_match=args.harvest_match,
        satellite_path=args.satellite,
        drone_path=args.drone,
        satellite_band=args.satellite_band,
        drone_band=args.drone_band,
        weights=weights,
        resolution_m=args.resolution,
        n_zones=args.zones,
        max_idw_distance_m=args.max_idw_distance,
    )
    area_ha = zones.to_crs("EPSG:32721").geometry.area.sum() / 10_000
    print(f"Zones exported: {len(zones)}")
    print(f"Mapped area: {area_ha:.2f} ha")
    print(f"Prescription ZIP: {Path(archive).resolve()}")


if __name__ == "__main__":
    main()
