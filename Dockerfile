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
# /knowledge is your folder (bind-mount it); /data holds the search index and
# token (a volume). Both are writable by any user, so the container can run
# as your own user and save pages that you own.
RUN useradd --create-home --uid 1000 docshelf \
    && mkdir /knowledge /data \
    && chmod 0777 /knowledge /data
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DOCSHELF_DIR=/knowledge \
    DOCSHELF_INDEX_DIR=/data \
    DOCSHELF_TRANSPORT=http \
    DOCSHELF_HOST=0.0.0.0 \
    DOCSHELF_PORT=8000
USER docshelf
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["docshelf"]
CMD ["serve"]
