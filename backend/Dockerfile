# ---------- build stage: compile wheels so the runtime image stays lean ----------
FROM python:3.14-slim AS build

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Build toolchain only lives in this stage (not shipped in the final image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Tunable at deploy time (see docs/DEPLOYMENT.md).
    WEB_CONCURRENCY=4

WORKDIR /app

# Runtime OS deps: tesseract for the OCR fallback; tini for correct signal
# handling (clean SIGTERM → graceful shutdown, no zombie procs); curl for the
# container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tini curl \
    && rm -rf /var/lib/apt/lists/*

# Install pre-built wheels — no compiler needed here.
COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .

# Run as an unprivileged user (never root).
RUN adduser --disabled-password --gecos "" --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness for orchestrators that honour it; k8s uses its own probes (see manifests).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# tini as PID 1 forwards signals so uvicorn shuts down gracefully. Worker count
# is WEB_CONCURRENCY (env).
#
# SEC-002: NO --proxy-headers here. `--forwarded-allow-ips '*'` made uvicorn
# copy the LEFTMOST, client-supplied X-Forwarded-For value into scope["client"],
# which is exactly the address the app's own spoofing-resistant rule
# (app/core/ratelimit.py::_client_ip, TRUSTED_PROXY_COUNT hops from the RIGHT)
# then trusted as "the socket peer" — so a forged header minted a fresh
# auth rate-limit bucket per request and forged the audit trail's ip column.
# The app reads X-Forwarded-For and X-Forwarded-Proto itself; set
# TRUSTED_PROXY_COUNT to the real number of hops (nginx origin = 1).
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-4}"]
