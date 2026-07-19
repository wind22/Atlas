FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ATLAS_APP_ROOT=/app \
    ATLAS_STATE_DIR=/var/lib/atlas \
    ATLAS_SEED_DIR=/opt/atlas-seed/public \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt ./
COPY atlas ./atlas
COPY templates ./templates
COPY web ./web
COPY reports ./reports
COPY data/atlas_snapshots.sqlite ./data/atlas_snapshots.sqlite

RUN pip install . \
    && mkdir -p /opt/atlas-seed/public \
    && python -m atlas --offline --db /tmp/atlas-seed.sqlite \
         --output /opt/atlas-seed/public/index.html \
    && cp -a web/. /opt/atlas-seed/public/ \
    && cp -f data/atlas_snapshots.sqlite /opt/atlas-seed/atlas_snapshots.sqlite

RUN if [ -f reports/backtest.html ]; then \
      cp reports/backtest.html /opt/atlas-seed/public/backtest.html; \
    fi \
    && if [ -f reports/backtest.json ]; then \
      cp reports/backtest.json /opt/atlas-seed/public/backtest.json; \
    fi

VOLUME ["/var/lib/atlas"]
EXPOSE 8080

CMD ["python", "-m", "atlas.server"]
