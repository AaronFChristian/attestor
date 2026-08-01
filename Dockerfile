FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 attestor
WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

RUN chown -R attestor:attestor /app
USER attestor

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
