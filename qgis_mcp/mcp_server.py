"""
Servidor MCP que controla una instancia activa de QGIS.

Requiere:
  pip install "mcp[cli]" httpx

Herramientas (stdio):
  cargar_capa(path)                     → POST /load
  recortar_al_lote(raster, mascara)     → POST /clip
  comparar_capas(raster_a, raster_b)    → POST /compare

Registro en el cliente (Claude Desktop / opencode):
  {
    "mcpServers": {
      "qgis": {
        "command": "python",
        "args": ["/ruta/a/qgis_mcp_server.py"]
      }
    }
  }
"""

import httpx
from mcp.server.fastmcp import FastMCP

QGIS_URL = "http://127.0.0.1:5000"
TIMEOUT = 130.0  # s; algo mayor que el timeout del listener

mcp = FastMCP(
    "qgis-assistant",
    instructions=(
        "Controla una instancia QGIS abierta. El listener debe estar corriendo "
        "en la consola de Python de QGIS (ver qgis_listener.py)."
    ),
)


def _post(path: str, payload: dict) -> dict:
    """POST JSON al listener de QGIS y normaliza la respuesta."""
    url = f"{QGIS_URL}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(url, json=payload)
    except httpx.ConnectError:
        return {
            "success": False,
            "error": (
                "No se pudo conectar con QGIS. ¿Está abierta la aplicación y "
                "ejecutándose el script qgis_listener.py en la consola de Python?"
            ),
        }
    except httpx.TimeoutException:
        return {"success": False, "error": f"QGIS no respondió en {TIMEOUT:.0f}s"}

    try:
        data = resp.json()
    except ValueError:
        return {
            "success": False,
            "error": f"Respuesta no válida de QGIS (HTTP {resp.status_code}): {resp.text[:300]}",
        }

    if not data.get("success", False) and "error" not in data:
        data["error"] = f"HTTP {resp.status_code}"
    return data


@mcp.tool()
def cargar_capa(path: str) -> dict:
    """Añade una capa vectorial (.shp, .gpkg, .geojson...) o ráster (.tif, .img...)
    al panel de capas del proyecto QGIS activo.

    Args:
        path: Ruta absoluta del archivo en el disco de la máquina que ejecuta QGIS.
    """
    return _post("/load", {"path": path})


@mcp.tool()
def recortar_al_lote(raster: str, mascara: str) -> dict:
    """Recorta una capa ráster activa con una máscara de recorte (capa vectorial
    de polígonos activa) usando gdal:cliprasterbymasklayer y añade el resultado
    al proyecto.

    Args:
        raster: Nombre exacto de la capa ráster cargada en QGIS.
        mascara: Nombre exacto de la capa vectorial de polígonos cargada en QGIS.
    """
    return _post("/clip", {"raster": raster, "mask": mascara})


@mcp.tool()
def comparar_capas(
    raster_a: str,
    raster_b: str,
    banda_a: int = 1,
    banda_b: int = 1,
) -> dict:
    """Resta dos capas ráster activas (A - B) con la Calculadora Ráster de QGIS
    y añade la capa de diferencia al proyecto.

    Args:
        raster_a: Nombre exacto de la capa ráster minuendo.
        raster_b: Nombre exacto de la capa ráster sustraendo.
        banda_a: Banda de raster_a a usar (por defecto 1). En capas RGB+NIR+NDVI:
            1=Rojo B4, 2=Verde B3, 3=Azul B2, 4=NIR B8, 5=NDVI.
        banda_b: Banda de raster_b a usar (por defecto 1).
    """
    return _post(
        "/compare",
        {
            "raster_a": raster_a,
            "raster_b": raster_b,
            "band_a": banda_a,
            "band_b": banda_b,
        },
    )


if __name__ == "__main__":
    mcp.run()  # transporte stdio por defecto
