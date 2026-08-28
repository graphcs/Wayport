FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so edits to src/ don't invalidate the layer.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1

# Shell form so ${PORT} is expanded by the shell at container start. Railway
# injects PORT at runtime, not build time, so it cannot be baked in.
CMD ["sh", "-c", "exec wayport relay --host 0.0.0.0 --port ${PORT:-8080}"]
