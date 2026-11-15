ARG REGISTRY_MIRROR=""
FROM ${REGISTRY_MIRROR}python:3.12-slim
ARG UV_INDEX_URL=https://pypi.org/simple
ENV UV_INDEX_URL=${UV_INDEX_URL} PIP_INDEX_URL=${UV_INDEX_URL} \
    UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv

# Layout mirrors the repo (/app/backend, /app/VERSION) so the editable install
# lets roracle.__version__ and the default alembic.ini resolve as in dev.
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
COPY VERSION /app/VERSION
RUN uv sync --frozen --no-dev

ENV PATH=/app/backend/.venv/bin:$PATH
RUN useradd --uid 10001 --no-create-home app
USER 10001
EXPOSE 8000
CMD ["python", "-m", "roracle", "api"]
