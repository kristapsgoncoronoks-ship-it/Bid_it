# ---------- build stage: compile wheels so the runtime image stays lean ----------
FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS build

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Build toolchain only lives in this stage (not shipped in the final image).
RUN --mount=type=secret,id=build_ca,target=/etc/ssl/certs/ca-certificates.crt,mode=0444 \
    apk add --no-cache build-base

COPY requirements.txt .
# Optional public CA bundle for corporate build proxies; never stored in the image.
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip wheel --wheel-dir /wheels -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Tunable at deploy time (see docs/DEPLOYMENT.md).
    WEB_CONCURRENCY=4

WORKDIR /app

# Runtime OS deps: tesseract for the OCR fallback; tini for correct signal
# handling (clean SIGTERM → graceful shutdown, no zombie procs). Python owns
# the health probe, so a separate curl executable is unnecessary.
RUN --mount=type=secret,id=build_ca,target=/etc/ssl/certs/ca-certificates.crt,mode=0444 \
    apk add --no-cache tesseract-ocr tesseract-ocr-data-eng tesseract-ocr-data-osd \
        font-dejavu tini gcompat libstdc++ \
    && mkdir -p /usr/share/fonts/truetype \
    && ln -s /usr/share/fonts/dejavu /usr/share/fonts/truetype/dejavu

# Install pre-built wheels — no compiler needed here.
COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels \
    && pip uninstall -y pip setuptools \
    && rm -rf /usr/local/lib/python3.14/ensurepip/_bundled

COPY . .

# Run as an unprivileged user. Preserve the shared-volume UID/GID contract.
# Docker copies the target owner/mode into an empty named volume; existing
# nonempty volumes keep their operator-managed ownership.
RUN addgroup -g 10001 appuser && adduser -D -u 10001 -G appuser appuser \
    && mkdir -p /app/var/storage \
    && chmod 0700 /app/var/storage \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness for orchestrators that honour it; k8s uses its own probes (see manifests).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3).close()" || exit 1

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
