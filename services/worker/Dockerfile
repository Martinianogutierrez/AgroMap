FROM qgis/qgis:3.40.3

ENV QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip python3-venv \
      python3-rasterio python3-geopandas python3-shapely \
      python3-scipy python3-sklearn python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY constraints.txt requirements.txt ./
RUN python3 -m venv --system-site-packages /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY pipeline ./pipeline
COPY worker ./worker
COPY tests ./tests
COPY scripts ./scripts
COPY pyproject.toml ./

EXPOSE 8080
RUN useradd -m worker && chown -R worker /app
USER worker

CMD ["python", "-m", "worker.main"]
