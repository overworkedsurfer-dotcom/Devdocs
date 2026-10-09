# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim

# ---- build: resolve dependencies into a virtualenv with uv ----
FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----
FROM ${PYTHON_IMAGE}
RUN useradd --create-home --uid 1000 devdocs \
    && mkdir /data \
    && chown devdocs /data
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DEVDOCS_DATA_DIR=/data \
    DEVDOCS_TRANSPORT=http \
    DEVDOCS_HOST=0.0.0.0 \
    DEVDOCS_PORT=8000
USER devdocs

# Optionally bake docs into the image: docker build --build-arg DOCS="python javascript" .
# A new named volume mounted at /data starts out with whatever was baked in.
ARG DOCS=""
RUN if [ -n "$DOCS" ]; then devdocs-mcp install $DOCS; fi

VOLUME /data
EXPOSE 8000
ENTRYPOINT ["devdocs-mcp"]
CMD ["serve"]
