FROM python:3.11-slim

ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md AGENTS.md ./
COPY docs ./docs
COPY schemas ./schemas
COPY swallow.capabilities.json ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts

RUN uv sync --dev --extra ci

CMD ["uv", "run", "pytest", "-q", "-m", "not network and not performance and not heavy"]
