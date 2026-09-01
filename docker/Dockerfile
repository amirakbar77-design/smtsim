# syntax=docker/dockerfile:1.7

# --- build ------------------------------------------------------------------
# The build stage installs dependencies into a virtualenv. It carries uv, the
# lockfile and a compiler toolchain; none of that reaches the final image.
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so editing a module does not reinstall FastAPI.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra service

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY configs/ ./configs/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra service

# --- runtime ----------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Non-root. The container never needs to write to its own filesystem.
RUN groupadd --system --gid 1001 smtsim \
 && useradd --system --uid 1001 --gid smtsim --create-home smtsim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=build --chown=smtsim:smtsim /app/.venv /app/.venv
COPY --from=build --chown=smtsim:smtsim /app/src /app/src
COPY --from=build --chown=smtsim:smtsim /app/migrations /app/migrations
COPY --from=build --chown=smtsim:smtsim /app/alembic.ini /app/alembic.ini
COPY --from=build --chown=smtsim:smtsim /app/configs /app/configs

USER smtsim

EXPOSE 8000

# No migration here. See the README: schema changes are an explicit,
# single-actor step, not something every replica races to do on boot.
CMD ["uvicorn", "smtsim_service.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
