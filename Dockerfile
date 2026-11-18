# syntax=docker/dockerfile:1.7
# One image for api, worker and scheduler; the command selects the role.

FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build -- --outDir /ui/dist

FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md constraints.txt ./
COPY assurance/ assurance/
COPY --from=ui /ui/dist assurance/ui/
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -c constraints.txt '.[otel]'

FROM python:3.13-slim
LABEL org.opencontainers.image.title="assurance" \
      org.opencontainers.image.description="Network snapshot and change assurance (Cisco IOS-XE / NX-OS)"
# Unicon drives the system ssh client, so live collection needs openssh-client.
# `assurance-admin backup/restore` need pg_dump/pg_restore matching the server (16), from the PGDG repo.
ARG APT_PACKAGES="openssh-client"
ARG PG_CLIENT=16
# Build with PG_CLIENT= APT_PACKAGES= where apt is unreachable (backup/restore then need a pg client elsewhere).
RUN if [ -n "$PG_CLIENT" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
      && install -d /usr/share/postgresql-common/pgdg \
      && curl -fsSo /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      && . /etc/os-release \
      && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
      && APT_PACKAGES="$APT_PACKAGES postgresql-client-${PG_CLIENT}"; \
    fi \
 && if [ -n "$APT_PACKAGES" ]; then apt-get update && apt-get upgrade -y \
      && apt-get install -y --no-install-recommends $APT_PACKAGES; fi \
 && if [ -n "$PG_CLIENT" ]; then apt-get purge -y curl && apt-get autoremove -y; fi \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /home/assurance --create-home assurance \
 && install -d -o assurance -m 0700 /backups
COPY --from=build /opt/venv /opt/venv
# Nothing installs packages at runtime, so drop pip (its vendored urllib3/msgpack only add CVEs).
RUN rm -rf /opt/venv/bin/pip* /opt/venv/lib/python3.13/site-packages/pip* \
           /usr/local/bin/pip* /usr/local/lib/python3.13/site-packages/pip*
COPY mock_data /app/mock_data
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 \
    ASSURANCE_MOCK_ROOT=/app/mock_data
WORKDIR /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "assurance.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
