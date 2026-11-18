# syntax=docker/dockerfile:1.7
# One image for api, worker and scheduler; the command selects the role.

FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build -- --outDir /ui/dist

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY assurance/ assurance/
COPY --from=ui /ui/dist assurance/ui/
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

FROM python:3.12-slim
LABEL org.opencontainers.image.title="assurance" \
      org.opencontainers.image.description="Network snapshot and change assurance (Cisco IOS-XE / NX-OS)"
# Unicon drives the system ssh client, so live collection needs openssh-client.
ARG APT_PACKAGES="openssh-client"
RUN if [ -n "$APT_PACKAGES" ]; then apt-get update && apt-get install -y --no-install-recommends $APT_PACKAGES && apt-get clean; fi \
 && useradd --system --uid 10001 --home /home/assurance --create-home assurance
COPY --from=build /opt/venv /opt/venv
COPY mock_data /app/mock_data
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 \
    ASSURANCE_MOCK_ROOT=/app/mock_data
WORKDIR /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "assurance.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
