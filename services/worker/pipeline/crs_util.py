from __future__ import annotations

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


def utm_crs_from_lonlat(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


def utm_crs_from_geom(geom: BaseGeometry) -> CRS:
    c = geom.centroid
    return utm_crs_from_lonlat(c.x, c.y)


def hemisphere_from_lat(lat: float) -> str:
    return "north" if lat >= 0 else "south"
