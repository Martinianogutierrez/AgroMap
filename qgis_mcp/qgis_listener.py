"""
QGIS Listener — ejecutar en la consola de Python de QGIS (Plugin > Consola de Python).

Lanza un servidor HTTP ligero en un hilo secundario (puerto 5000) que expone:
  POST /load    {"path": "..."}
  POST /clip    {"raster": "nombre", "mask": "nombre_poligono"}
  POST /compare {"raster_a": "nombre", "raster_b": "nombre",
                 "band_a": 5, "band_b": 5}   # bandas opcionales, por defecto 1
  POST /layers  {}                      → lista capas activas (nombres, tipo, bandas)

TODO el acceso a iface / QgsProject se encola en el hilo principal de Qt
via señal pyqtSignal, evitando congelar o romper la interfaz de QGIS.

Uso: copiar todo el contenido en la consola de Python de QGIS y ejecutar.
Detener: QGIS_MCP.stop()
"""

import json
import os
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.utils import iface
from qgis.PyQt.QtCore import QObject, pyqtSignal

PORT = 5000
RESPONSE_TIMEOUT = 120  # segundos


class _Bridge(QObject):
    """Encola peticiones del hilo del servidor al hilo principal de Qt."""
    dispatch = pyqtSignal(object)


_bridge = _Bridge()
_server = None
_thread = None


# ----------------------------------------------------------------------------
# Handlers (siempre corren en el hilo principal de QGIS)
# ----------------------------------------------------------------------------

def _find_layer(name):
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr.name() == name:
            return lyr
    raise ValueError(f"No se encontró ninguna capa activa con el nombre '{name}'")


def handle_load(params):
    path = params["path"]
    if not os.path.exists(path):
        raise FileNotFoundError(f"El archivo no existe: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext in (".shp", ".gpkg", ".geojson", ".json", ".kml", ".gml", ".tab"):
        layer = QgsVectorLayer(path, os.path.basename(path), "ogr")
    elif ext in (".tif", ".tiff", ".img", ".vrt", ".asc", ".jp2", ".nc"):
        layer = QgsRasterLayer(path, os.path.basename(path))
    else:
        raise ValueError(f"Extensión no soportada: {ext}")

    if not layer.isValid():
        raise ValueError(f"QGIS no pudo abrir la capa: {path}")

    QgsProject.instance().addMapLayer(layer)
    iface.mapCanvas().refresh()
    return {"success": True, "message": f"Capa '{layer.name()}' añadida al proyecto"}


def handle_clip(params):
    import processing

    raster = _find_layer(params["raster"])
    mask = _find_layer(params["mask"])

    if not isinstance(raster, QgsRasterLayer):
        raise ValueError(f"'{params['raster']}' no es una capa ráster")
    if not isinstance(mask, QgsVectorLayer):
        raise ValueError(f"'{params['mask']}' no es una capa vectorial")

    out_path = os.path.join(
        tempfile.gettempdir(),
        f"qgis_mcp_clip_{os.path.splitext(os.path.basename(raster.source()))[0]}.tif",
    )

    result = processing.run(
        "gdal:cliprasterbymasklayer",
        {
            "INPUT": raster,
            "MASK": mask,
            "SOURCE_CRS": None,
            "TARGET_CRS": None,
            "NODATA": None,
            "ALPHA_BAND": False,
            "CROP_TO_CUTLINE": True,
            "KEEP_RESOLUTION": True,
            "SET_RESOLUTION": False,
            "XRES": None,
            "YRES": None,
            "MULTITHREADING": True,
            "OPTIONS": "",
            "DATA_TYPE": 0,
            "EXTRA": "",
            "OUTPUT": out_path,
        },
        is_child_algorithm=True,
    )

    clipped = QgsRasterLayer(result["OUTPUT"], f"{raster.name()}_clip")
    if not clipped.isValid():
        raise ValueError("El resultado del recorte no es una capa válida")

    QgsProject.instance().addMapLayer(clipped)
    iface.mapCanvas().refresh()
    return {
        "success": True,
        "message": f"Recorte '{clipped.name()}' generado y añadido al proyecto",
        "output": result["OUTPUT"],
    }


def handle_compare(params):
    import processing

    layer_a = _find_layer(params["raster_a"])
    layer_b = _find_layer(params["raster_b"])

    if not isinstance(layer_a, QgsRasterLayer) or not isinstance(layer_b, QgsRasterLayer):
        raise ValueError("Ambas capas deben ser ráster")

    # La Calculadora Ráster (qgis:rastercalculator) referencia capas por
    # nombre con banda:  "nombre_capa@N"
    band_a = int(params.get("band_a", 1))
    band_b = int(params.get("band_b", 1))
    for lyr, band, key in ((layer_a, band_a, "raster_a"), (layer_b, band_b, "raster_b")):
        if lyr.bandCount() < band or band < 1:
            raise ValueError(
                f"'{lyr.name()}' tiene {lyr.bandCount()} banda(s); "
                f"la banda {band} pedida para {key} no existe"
            )

    expression = f'"{layer_a.name()}@{band_a}" - "{layer_b.name()}@{band_b}"'  # A - B

    out_path = os.path.join(
        tempfile.gettempdir(),
        f"qgis_mcp_diff_{layer_a.name()}_{layer_b.name()}_b{band_a}-b{band_b}.tif",
    )

    result = processing.run(
        "qgis:rastercalculator",
        {
            "EXPRESSION": expression,
            "LAYERS": [layer_a, layer_b],
            "CELL_SIZE": 0,
            "CRS": None,
            "EXTENT": None,
            "OUTPUT": out_path,
        },
        is_child_algorithm=True,
    )

    diff = QgsRasterLayer(
        result["OUTPUT"],
        f"diff_{layer_a.name()}_{layer_b.name()}_b{band_a}-b{band_b}",
    )
    if not diff.isValid():
        raise ValueError("El resultado de la resta no es una capa válida")

    QgsProject.instance().addMapLayer(diff)
    iface.mapCanvas().refresh()
    return {
        "success": True,
        "message": f"Diferencia '{diff.name()}' generada y añadida al proyecto",
        "output": result["OUTPUT"],
    }


def handle_layers(params):
    """Lista las capas activas del proyecto (para que el asistente descubra nombres)."""
    out = []
    for lyr in QgsProject.instance().mapLayers().values():
        info = {
            "name": lyr.name(),
            "type": "raster" if isinstance(lyr, QgsRasterLayer) else "vector",
            "source": lyr.source(),
        }
        if isinstance(lyr, QgsRasterLayer):
            info["bands"] = lyr.bandCount()
            info["band_names"] = [
                lyr.bandName(i + 1) for i in range(min(lyr.bandCount(), 8))
            ]
        else:
            info["geometry"] = lyr.geometryType()
        out.append(info)
    return {"success": True, "layers": out}


ROUTES = {
    "load": handle_load,
    "clip": handle_clip,
    "compare": handle_compare,
    "layers": handle_layers,
}


# ----------------------------------------------------------------------------
# Servidor HTTP (hilo secundario) — solo encola trabajo en el hilo principal
# ----------------------------------------------------------------------------

def _run_in_main_thread(name, params, timeout=RESPONSE_TIMEOUT):
    """Ejecuta un handler en el hilo principal de Qt y devuelve su resultado."""
    event = threading.Event()
    holder = {}

    def _done(res):
        holder["response"] = res
        event.set()

    payload = {"_handler": name, "_params": params, "_done": _done}
    _bridge.dispatch.emit(payload)

    if not event.wait(timeout):
        raise TimeoutError(f"QGIS no respondió en {timeout}s")
    return holder["response"]


def _main_thread_worker(payload):
    name = payload["_handler"]
    params = payload["_params"]
    try:
        response = ROUTES[name](params)
    except Exception as exc:
        traceback.print_exc()
        response = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    payload["_done"](response)


_bridge.dispatch.connect(_main_thread_worker)  # conexión en cola → hilo principal


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        route = self.path.rstrip("/").lstrip("/")
        if route not in ROUTES:
            self._respond(404, {"success": False, "error": f"Ruta desconocida: {self.path}"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            params = json.loads(body or b"{}")
            response = _run_in_main_thread(route, params)
            status = 200 if response.get("success") else 500
        except Exception as exc:
            response = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
            status = 500

        self._respond(status, response)

    def _respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[QGIS-MCP] {fmt % args}")


# ----------------------------------------------------------------------------
# Arranque / parada
# ----------------------------------------------------------------------------

def _start():
    global _server, _thread
    if _server is not None:
        print("El servidor QGIS-MCP ya está corriendo en el puerto", PORT)
        return

    _server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()

    class _Stop:
        def __call__(self):
            global _server, _thread
            if _server:
                _server.shutdown()
                _server.server_close()
                _server = None
                _thread = None
                print("Servidor QGIS-MCP detenido.")
            else:
                print("El servidor no está en marcha.")

        def __repr__(self):
            return "<QGIS_MCP.stop>"

    import builtins
    builtins.QGIS_MCP = _Stop()
    print(f"Servidor QGIS-MCP escuchando en http://127.0.0.1:{PORT}")
    print("Endpoints: POST /load, POST /clip, POST /compare")
    print("Detener con: QGIS_MCP.stop()")


_start()
