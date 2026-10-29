"""
Agro QGIS Assistant — PoC web backend (FastAPI).

Expone:
  POST /api/chat   → recibe un mensaje NL, decide una acción (tool calling) y
                     la ejecuta contra el listener de QGIS en http://127.0.0.1:5000
                     (endpoints /load, /clip, /compare, /layers).
  GET  /api/status → estado del listener de QGIS y modo del backend.
  GET  /           → interfaz web (static/index.html).

Modos de comprensión de lenguaje natural:
  * LLM  — si existen LLM_API_KEY (u OPENAI_API_KEY): se usa un modelo
           compatible con OpenAI con function calling real (herramientas
           cargar_capa / recortar_al_lote / comparar_capas / listar_capas).
  * FALLBACK — sin clave: analizador de intenciones por palabras clave +
           resolución de nombres contra las capas activas de QGIS (/layers).

Variables de entorno:
  QGIS_URL     (def. http://127.0.0.1:5000)
  LLM_API_KEY  / OPENAI_API_KEY
  LLM_BASE_URL (def. https://api.openai.com/v1)
  LLM_MODEL    (def. gpt-4o-mini)

Ejecutar:  uvicorn app:app --port 8000   (ver README.md)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

QGIS_URL = os.getenv("QGIS_URL", "http://127.0.0.1:5000").rstrip("/")
QGIS_TIMEOUT = 130.0

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

STATIC_DIR = Path(__file__).parent / "static"

# Archivos conocidos del proyecto: alias → ruta (para el modo fallback y para
# que el LLM tenga ejemplos reales en su prompt).
PROJECT = Path(__file__).resolve().parents[2]
SAT_DIR = PROJECT / "Asserts" / "03 - Imágenes satelitales"
KNOWN_FILES = {
    "ndvi 2025": str(SAT_DIR / "2025" / "Sentinel-2 2025-06 - RGB NIR NDVI.tif"),
    "ndvi 2024": str(SAT_DIR / "2024" / "Sentinel-2 2024-06 - RGB NIR NDVI.tif"),
    "límite del lote": str(PROJECT / "Asserts" / "01 - Lote y ortomosaico" / "Limite de trabajo.gpkg"),
    "limite del lote": str(PROJECT / "Asserts" / "01 - Lote y ortomosaico" / "Limite de trabajo.gpkg"),
}

MONTHS = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

app = FastAPI(title="Agro QGIS Assistant PoC", version="0.1.0")


# ----------------------------------------------------------------------------
# Capas de integración con el listener de QGIS
# ----------------------------------------------------------------------------

def qgis_post(path: str, payload: Optional[dict] = None) -> dict[str, Any]:
    """POST JSON al listener de QGIS y devuelve la respuesta normalizada."""
    try:
        with httpx.Client(timeout=QGIS_TIMEOUT) as client:
            resp = client.post(f"{QGIS_URL}{path}", json=payload or {})
    except httpx.ConnectError:
        return {
            "success": False,
            "error": "QGIS no responde. Comprueba que QGIS está abierto y que "
                     "qgis_listener.py se está ejecutando en su consola de Python.",
        }
    except httpx.TimeoutException:
        return {"success": False, "error": f"QGIS no respondió en {QGIS_TIMEOUT:.0f}s"}
    try:
        return resp.json()
    except ValueError:
        return {"success": False, "error": f"Respuesta inválida de QGIS (HTTP {resp.status_code})"}


def qgis_layers() -> list[dict[str, Any]]:
    """Lista de capas activas ([] si el listener no responde)."""
    data = qgis_post("/layers")
    return data.get("layers", []) if data.get("success") else []


# ----------------------------------------------------------------------------
# Ejecución de herramientas (compartida por LLM y fallback)
# ----------------------------------------------------------------------------

def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una herramienta contra QGIS y devuelve su resultado."""
    if name == "cargar_capa":
        return qgis_post("/load", {"path": args["path"]})
    if name == "recortar_al_lote":
        return qgis_post("/clip", {"raster": args["raster"], "mask": args["mascara"]})
    if name == "comparar_capas":
        payload = {
            "raster_a": args["raster_a"],
            "raster_b": args["raster_b"],
            "band_a": args.get("banda_a", 1),
            "band_b": args.get("banda_b", 1),
        }
        return qgis_post("/compare", payload)
    if name == "listar_capas":
        layers = qgis_layers()
        if not layers:
            return {"success": False, "error": "No hay capas activas o QGIS no responde"}
        return {"success": True, "layers": layers}
    return {"success": False, "error": f"Herramienta desconocida: {name}"}


TOOL_SPECS = [
    {
        "name": "cargar_capa",
        "description": "Añade una capa vectorial o ráster (por ruta absoluta) al proyecto QGIS activo.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta absoluta del archivo (.tif, .shp, .gpkg...)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "recortar_al_lote",
        "description": "Recorta un ráster activo con una máscara de polígonos activa (gdal:cliprasterbymasklayer).",
        "parameters": {
            "type": "object",
            "properties": {
                "raster": {"type": "string", "description": "Nombre exacto de la capa ráster activa"},
                "mascara": {"type": "string", "description": "Nombre exacto de la capa vectorial de polígonos activa"},
            },
            "required": ["raster", "mascara"],
        },
    },
    {
        "name": "comparar_capas",
        "description": "Resta dos rásteres activos (A - B) con la Calculadora Ráster de QGIS y muestra la diferencia.",
        "parameters": {
            "type": "object",
            "properties": {
                "raster_a": {"type": "string", "description": "Nombre del ráster minuendo (A)"},
                "raster_b": {"type": "string", "description": "Nombre del ráster sustraendo (B)"},
                "banda_a": {"type": "integer", "description": "Banda de A (por defecto 1; p. ej. 5=NDVI en Sentinel-2 RGB+NIR+NDVI)"},
                "banda_b": {"type": "integer", "description": "Banda de B (por defecto 1)"},
            },
            "required": ["raster_a", "raster_b"],
        },
    },
    {
        "name": "listar_capas",
        "description": "Lista las capas activas en QGIS (nombres exactos, tipo, bandas). Úsala antes de recortar o comparar si no conoces los nombres.",
        "parameters": {"type": "object", "properties": {}},
    },
]


# ----------------------------------------------------------------------------
# Modo LLM: bucle de tool calling compatible con OpenAI
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Eres un asistente agrícola (agro-tech) que controla una instancia de QGIS "
    "en tiempo real a través de herramientas. Trabajas con imágenes Sentinel-2 "
    "(RGB+NIR+NDVI; banda 5 = NDVI) y límites de lotes. "
    "Reglas: usa las herramientas para ejecutar acciones en QGIS; si necesitas "
    "nombres exactos de capas, llama a listar_capas primero; responde en español, "
    "breve y concreto, indicando siempre si la acción en QGIS tuvo éxito o falló "
    "y por qué. Al comparar capas, si el usuario pide NDVI usa la banda NDVI (5)."
)


def llm_chat(messages: list[dict], tools: Optional[list[dict]] = None) -> dict:
    """Una llamada al chat completions compatible con OpenAI."""
    body: dict[str, Any] = {"model": LLM_MODEL, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


def run_llm(message: str, history: list[dict]) -> tuple[str, list[dict]]:
    """Bucle de tool calling (máx. 4 rondas). Devuelve (respuesta, acciones)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": message})
    actions: list[dict] = []

    openai_tools = [{"type": "function", "function": t} for t in TOOL_SPECS]

    for _ in range(4):
        msg = llm_chat(messages, tools=openai_tools)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return msg.get("content") or "(sin respuesta)", actions

        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            result = execute_tool(fn["name"], args)
            actions.append({"tool": fn["name"], "args": args, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps(result, ensure_ascii=False),
            })

    return "(No se completó la acción en 4 iteraciones.)", actions


# ----------------------------------------------------------------------------
# Modo fallback: analizador de intenciones (sin LLM)
# ----------------------------------------------------------------------------

def _quoted(text: str) -> list[str]:
    return re.findall(r'["“”\']([^"“”\']{2,})["“”\']', text)


def _pick_file(text: str) -> Optional[str]:
    """Ruta absoluta mencionada o alias conocido."""
    m = re.search(r"(/[\w .\-/()]+\.(?:tif|tiff|shp|gpkg|geojson|json|img|vrt))", text, re.I)
    if m:
        return m.group(1).strip()
    low = text.lower()
    for alias, path in KNOWN_FILES.items():
        if alias in low:
            return path
    return None


def _resolve_by_query(query: str, layers: list[dict], kind: str) -> Optional[str]:
    """Resuelve un texto del usuario contra nombres de capas activas."""
    q = query.lower()
    cand = [l for l in layers if l.get("type") == kind]
    # coincidencia exacta o por subcadena
    for l in cand:
        if q == l["name"].lower() or q in l["name"].lower() or l["name"].lower() in q:
            return l["name"]
    # todos los tokens del query presentes en el nombre
    tokens = [t for t in re.split(r"\W+", q) if len(t) > 2]
    scored = sorted(
        cand,
        key=lambda l: sum(1 for t in tokens if t in l["name"].lower()),
        reverse=True,
    )
    if scored and tokens and sum(1 for t in tokens if t in scored[0]["name"].lower()) >= 2:
        return scored[0]["name"]
    return None


def _ndvi_band(layer: dict) -> int:
    names = [n.lower() for n in layer.get("band_names", [])]
    for i, n in enumerate(names, start=1):
        if "ndvi" in n:
            return i
    return 1


def _format_layers(layers: list[dict]) -> str:
    if not layers:
        return "No hay capas activas en QGIS."
    lines = ["Capas activas en QGIS:"]
    for l in layers:
        extra = f", {l.get('bands')} bandas" if l.get("type") == "raster" else ""
        lines.append(f"- [{l['type']}] {l['name']}{extra}")
    return "\n".join(lines)


def run_fallback(message: str, _history: list[dict]) -> tuple[str, list[dict]]:
    """Intención por palabras clave. Devuelve (respuesta, acciones)."""
    low = message.lower()
    actions: list[dict] = []
    quoted = _quoted(message)

    # --- comparar -----------------------------------------------------------
    if any(k in low for k in ("compar", "resta", "diferenc", " restar", "diff")):
        layers = qgis_layers()
        rasters = [l for l in layers if l.get("type") == "raster"]
        a = _resolve_by_query(quoted[0], layers, "raster") if len(quoted) >= 2 else None
        b = _resolve_by_query(quoted[1], layers, "raster") if len(quoted) >= 2 else None

        if not (a and b):
            # "junio 2025" / "2025-06" → capa que contiene ese periodo; si no,
            # un año mencionado por capa (una por año)
            periods: list[str] = []
            for mo_name, mo_num in MONTHS.items():
                for yr in re.findall(r"20\d\d", message):
                    if mo_name in low:
                        periods.append(f"{yr}-{mo_num}")
            periods += re.findall(r"20\d\d-\d\d", message)
            periods += sorted(set(re.findall(r"20\d\d", message)), reverse=True)
            for q in periods:
                match = next((l["name"] for l in rasters if q in l["name"]), None)
                if match and not a:
                    a = match
                elif match and not b and match != a:
                    b = match
        if not (a and b):
            return (
                "No pude identificar las dos capas a comparar. "
                + _format_layers(layers)
                + '\nEscríbelas entre comillas, p. ej.: '
                  '"compara \'Sentinel-2 2025-06 - RGB NIR NDVI.tif\' con \'Sentinel-2 2025-05 - RGB NIR NDVI.tif\'"',
                actions,
            )

        la = next((l for l in rasters if l["name"] == a), {})
        lb = next((l for l in rasters if l["name"] == b), {})
        banda = 1
        if "ndvi" in low:
            banda = max(_ndvi_band(la), _ndvi_band(lb))
        args = {"raster_a": a, "raster_b": b, "banda_a": banda, "banda_b": banda}
        result = execute_tool("comparar_capas", args)
        actions.append({"tool": "comparar_capas", "args": args, "result": result})
        if result.get("success"):
            return (
                f"He restado {a} − {b} (banda {banda}{' = NDVI' if banda > 1 else ''}). "
                f"Capa añadida al proyecto: {result.get('message', '')}",
                actions,
            )
        return f"Error al comparar: {result.get('error', 'error desconocido')}", actions

    # --- recortar -----------------------------------------------------------
    if any(k in low for k in ("recort", "clip", "máscara", "mascara", "boundary")):
        layers = qgis_layers()
        mask = None
        raster = None

        # máscara: keyword de límite, o segundo texto entrecomillado
        for key in ("límite", "limite", "boundary", "lote"):
            if key in low:
                mask = _resolve_by_query("limite", layers, "vector") or _resolve_by_query("lote", layers, "vector")
                break
        if not mask and len(quoted) >= 2:
            mask = _resolve_by_query(quoted[1], layers, "vector")
        if not mask:
            vectors = [l for l in layers if l.get("type") == "vector"]
            if len(vectors) == 1:
                mask = vectors[0]["name"]

        if quoted:
            raster = _resolve_by_query(quoted[0], layers, "raster")
        if not raster:
            rasters = [l for l in layers if l.get("type") == "raster"]
            years = re.findall(r"20\d\d", message)
            for yr in years:
                hit = next((l["name"] for l in rasters if yr in l["name"]), None)
                if hit:
                    raster = hit
                    break
            if not raster and rasters:
                raster = rasters[0]["name"]

        if not (raster and mask):
            return (
                "Necesito el ráster y la máscara de recorte.\n" + _format_layers(layers)
                + "\nEjemplo: recorta \"NDVI\" usando \"límite del lote\"",
                actions,
            )
        args = {"raster": raster, "mascara": mask}
        result = execute_tool("recortar_al_lote", args)
        actions.append({"tool": "recortar_al_lote", "args": args, "result": result})
        if result.get("success"):
            return f"Recorte realizado: {result.get('message', '')} (salida: {result.get('output', '')})", actions
        return f"Error al recortar: {result.get('error', 'error desconocido')}", actions

    # --- cargar -------------------------------------------------------------
    if any(k in low for k in ("carga", "cargar", "load", "añad", "agreg", "abre", "open")):
        path = _pick_file(message)
        if not path:
            return (
                "No encuentro la ruta del archivo. Menciónala de forma absoluta, "
                'p. ej.: carga "/ruta/archivo.tif", o usa un alias como "NDVI 2025".',
                actions,
            )
        args = {"path": path}
        result = execute_tool("cargar_capa", args)
        actions.append({"tool": "cargar_capa", "args": args, "result": result})
        if result.get("success"):
            return f"{result.get('message', '')} (archivo: {path})", actions
        return f"Error al cargar: {result.get('error', 'error desconocido')}", actions

    # --- listar / ayuda -----------------------------------------------------
    if any(k in low for k in ("lista", "listar", "qué capas", "que capas", "layers", "hay en qgis")):
        layers = qgis_layers()
        return _format_layers(layers), actions

    return (
        "No entendí la acción. Puedo: cargar capas, recortar un ráster con una "
        'máscara y comparar/restar rásteres. Ejemplos:\n'
        '- Carga el NDVI 2025\n'
        '- Recorta el NDVI usando el límite del lote\n'
        '- Calcula la diferencia entre NDVI 2025 y 2024',
        actions,
    )


# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------

class ChatIn(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/api/chat")
def chat(body: ChatIn) -> JSONResponse:
    message = body.message.strip()
    if not message:
        return JSONResponse({"reply": "Mensaje vacío.", "actions": [], "mode": "n/a"})

    if LLM_API_KEY:
        try:
            reply, actions = run_llm(message, body.history)
            mode = f"llm:{LLM_MODEL}"
        except Exception as exc:  # el LLM cae → fallback local
            reply, actions = run_fallback(message, body.history)
            reply = f"(LLM no disponible: {exc})\n{reply}"
            mode = "fallback"
    else:
        reply, actions = run_fallback(message, body.history)
        mode = "fallback"

    qgis_ok = bool(actions) and all(a["result"].get("success") for a in actions)
    return JSONResponse({
        "reply": reply,
        "mode": mode,
        "actions": actions,
        "qgis_ok": qgis_ok if actions else None,
    })


@app.get("/api/status")
def status() -> dict:
    reachable = qgis_post("/layers", {}).get("success", False)
    return {
        "qgis": reachable,
        "mode": f"llm:{LLM_MODEL}" if LLM_API_KEY else "fallback",
        "qgis_url": QGIS_URL,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
