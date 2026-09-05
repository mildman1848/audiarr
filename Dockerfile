ARG LSIO_BASE_VERSION=noble
FROM ghcr.io/linuxserver/baseimage-ubuntu:${LSIO_BASE_VERSION}

ARG LSIO_BASE_VERSION
ARG APP_VERSION=0.1.0
ARG IMAGE_REVISION=mldm1
ARG VERSION=0.1.0-mldm1
ARG BUILD_DATE=unknown
ARG VCS_REF=unknown

ENV APP_VERSION="${APP_VERSION}" \
    IMAGE_REVISION="${IMAGE_REVISION}" \
    VERSION="${VERSION}" \
    PYTHONUNBUFFERED=1 \
    AUDIARR_HOST=0.0.0.0 \
    AUDIARR_PORT=8787 \
    AUDIARR_CONFIG_DIR=/config \
    AUDIARR_DEFAULT_ROOT_FOLDER=/data/audiobooks

LABEL org.opencontainers.image.title="audiarr" \
      org.opencontainers.image.description="Servarr-style audiobook manager (metadata, library, connections to Audiobookshelf and m4b-convertarr)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.base.name="ghcr.io/linuxserver/baseimage-ubuntu:${LSIO_BASE_VERSION}" \
      org.opencontainers.image.source="https://github.com/mildman1848/audiarr" \
      org.opencontainers.image.licenses="MIT" \
      build_version="Mildman1848 audiarr version:- ${VERSION} Upstream:- ${APP_VERSION} Revision:- ${IMAGE_REVISION}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY app /app/audiarr/app
COPY requirements.txt /app/audiarr/requirements.txt
RUN chown -R abc:abc /app/audiarr/app /app/audiarr/requirements.txt \
    && python3 -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /app/venv/bin/pip install --no-cache-dir -r /app/audiarr/requirements.txt \
    && chown -R abc:abc /app/venv

COPY root/ /
RUN chmod +x /usr/local/bin/start-audiarr-api \
    && chmod +x /etc/s6-overlay/s6-rc.d/init-audiarr/run /etc/s6-overlay/s6-rc.d/audiarr-api/run

EXPOSE 8787
VOLUME ["/config", "/data"]
