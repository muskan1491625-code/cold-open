# Cold Open: one image serving the API and the built review UI on port 7860.

# --- build the frontend ---------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- python runtime -------------------------------------------------------
FROM python:3.12-slim
# COLDOPEN_REQUIRE_CODE=1: if DEMO_ACCESS_CODE is missing, client runs stay locked (fail closed).
# COLDOPEN_PROXY_HOPS=1: hosted behind one reverse proxy (Hugging Face, Render), so read the
# client address it appends; wrong-code lockouts are then per visitor, not shared by everyone.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    COLDOPEN_HOME=/app COLDOPEN_RUNS=/data/runs COLDOPEN_WEB=/app/web/dist \
    COLDOPEN_PROXY_HOPS=1 COLDOPEN_REQUIRE_CODE=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.5.4
COPY pyproject.toml uv.lock README.md ./
COPY coldopen/ coldopen/
RUN uv pip install --system --no-cache .
COPY config.yaml prices.yaml ./
COPY scripts/fetch_demo.py scripts/fetch_demo.py
COPY --from=web /web/dist web/dist
# Hugging Face runs containers as uid 1000; give it a writable data dir.
RUN useradd -m -u 1000 app && mkdir -p /data/runs /app/.cache && chown -R app /data /app/.cache
# Demo runs staged by scripts/stage_demo.py (empty in git; filled only for a CLI deploy).
COPY --chown=1000 demo_runs/ /data/runs/
USER app
EXPOSE 7860
# Demo runs come either from demo_runs/ (baked in by a CLI deploy) or, at start,
# from DEMO_RUNS_URL (a private location) via fetch_demo.py.
CMD ["sh", "-c", "python scripts/fetch_demo.py && uvicorn coldopen.api.server:app --host 0.0.0.0 --port ${PORT:-7860} --no-server-header"]
