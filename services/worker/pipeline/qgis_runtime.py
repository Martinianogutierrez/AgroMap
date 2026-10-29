from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@contextmanager
def qgis_session() -> Iterator[Any]:
    """One QgsApplication per process. Prefer scipy fallbacks in unit tests."""
    try:
        from qgis.core import QgsApplication  # type: ignore
    except ImportError as e:
        raise RuntimeError("PyQGIS not available") from e

    app = QgsApplication([], False)
    app.initQgis()
    plugins = "/usr/share/qgis/python/plugins"
    if plugins not in sys.path:
        sys.path.append(plugins)
    import processing  # type: ignore
    from processing.core.Processing import Processing  # type: ignore
    from qgis.analysis import QgsNativeAlgorithms  # type: ignore

    Processing.initialize()
    QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
    try:
        yield processing
    finally:
        app.exitQgis()


def run_algorithm(processing: Any, alg_id: str, params: dict, context: Any = None) -> dict:
    try:
        result = processing.run(alg_id, params, context=context)
        return result
    except Exception as e:
        log.exception("QGIS algorithm %s failed", alg_id)
        raise RuntimeError(f"QGIS algorithm {alg_id} failed: {e}") from e


def qgis_available() -> bool:
    try:
        import qgis.core  # noqa: F401

        return True
    except ImportError:
        return False
