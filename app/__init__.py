"""Audiarr backend package.

Audiarr is a Servarr-style audiobook manager. This package contains the
FastAPI application, settings models, metadata provider abstraction,
outbound connection clients, and the minimal server-rendered web UI.
"""

__all__ = ["__version__"]

# Kept in sync with Dockerfile ARG APP_VERSION and docker-compose.yml.
__version__ = "0.1.0"
