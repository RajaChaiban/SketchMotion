FROM python:3.12-slim

# System deps: ffmpeg (encode/compose), fonts for PIL text rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uv for dependency management.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml ./
COPY .python-version ./
RUN uv sync --no-install-project

COPY . .

ENV OUTPUT_DIR=/data/outputs
RUN mkdir -p /data/outputs

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
