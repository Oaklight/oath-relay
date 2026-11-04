ARG REGISTRY_MIRROR=docker.io

FROM ${REGISTRY_MIRROR}/library/python:3.12-slim AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build \
 && python -m build --wheel --outdir /wheels

FROM ${REGISTRY_MIRROR}/library/python:3.12-slim

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PUID=${APP_UID} \
    PGID=${APP_GID}

# gosu is only needed so the entrypoint can drop from root to $PUID:$PGID.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

RUN groupadd -g "${APP_GID}" oath \
 && useradd -u "${APP_UID}" -g "${APP_GID}" -M -s /usr/sbin/nologin oath \
 && mkdir -p /state /config \
 && chown -R "${APP_UID}:${APP_GID}" /state /config

COPY docker/entrypoint.sh /usr/local/bin/oath-relay-entrypoint
RUN chmod +x /usr/local/bin/oath-relay-entrypoint

VOLUME ["/state"]
EXPOSE 8790

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/oath-relay-entrypoint"]
CMD ["serve", "--config", "/config/config.jsonc"]
