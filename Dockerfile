# JobHub - single small image, non-root, no compilers in the final stage.
# PYTHON_IMAGE can point at an internal registry/mirror for air-gapped builds:
#   docker build --build-arg PYTHON_IMAGE=registry.internal/python:3.12-slim .
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
# Optional: internal package mirror (e.g. Nexus/Artifactory PyPI proxy).
ARG PIP_INDEX_URL=""
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt requirements-dev.txt ./
# Optional CA bundle for TLS-intercepting proxies, passed as a build secret so it
# never ends up in an image layer:  --secret id=ca_bundle,src=/path/to/ca.pem
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -e; \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; else unset PIP_INDEX_URL; fi; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install -r requirements.txt; \
    python -m venv /opt/venv-dev; \
    /opt/venv-dev/bin/pip install -r requirements-dev.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    JOBHUB_DATA_DIR=/data \
    JOBHUB_HOST=0.0.0.0 \
    JOBHUB_PORT=8080
RUN groupadd --system --gid 10001 jobhub \
 && useradd --system --uid 10001 --gid jobhub --home-dir /app --shell /usr/sbin/nologin jobhub \
 && mkdir -p /data && chown jobhub:jobhub /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root app ./app
COPY --chown=root:root pyproject.toml ./
USER jobhub
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]
# exec form -> uvicorn is PID 1 and receives SIGTERM for a graceful shutdown
CMD ["python", "-m", "app.cli", "serve"]

# Test image: `docker build --target test -t jobhub:test .`
FROM runtime AS test
USER root
COPY --from=build /opt/venv-dev /opt/venv-dev
COPY tests ./tests
ENV PATH="/opt/venv-dev/bin:$PATH"
RUN chown -R jobhub:jobhub /app
USER jobhub
CMD ["python", "-m", "pytest", "-q"]
