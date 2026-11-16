ARG REGISTRY_MIRROR=""
FROM ${REGISTRY_MIRROR}python:3.12-slim
ARG UV_INDEX_URL=https://pypi.org/simple
ARG VERSION=0.0.0
LABEL org.opencontainers.image.title="roracle-backend" org.opencontainers.image.version="${VERSION}"
ENV PYTHONUNBUFFERED=1 HOME=/tmp
RUN pip install --no-cache-dir --index-url "$UV_INDEX_URL" uv==0.10.4

# Layout mirrors the repo (/app/backend, /app/VERSION) so the editable install
# lets roracle.__version__ and the default alembic.ini resolve as in dev.
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
# Hash-pinned requirements exported from the lock (no pypi.org/pythonhosted URLs), installed from the mirror.
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req.txt \
 && uv venv .venv \
 && uv pip install --python .venv --require-hashes --index-url "$UV_INDEX_URL" -r /tmp/req.txt
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
COPY VERSION /app/VERSION
RUN uv pip install --python .venv --no-deps --index-url "$UV_INDEX_URL" -e .

ENV PATH=/app/backend/.venv/bin:$PATH
RUN useradd --uid 10001 --no-create-home app
USER 10001
EXPOSE 8000
CMD ["python", "-m", "roracle", "api"]
