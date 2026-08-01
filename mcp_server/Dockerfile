# Isolated from the main Dockerfile deliberately — see requirements.txt for
# why. This image never installs fastapi/starlette at all.
FROM python:3.11-slim

RUN useradd --create-home --uid 1000 attestor
WORKDIR /app

COPY mcp_server/requirements.txt /app/mcp_server/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r mcp_server/requirements.txt

# Only the source the server actually imports: app.core.* and app.models.*
# (config, database, ORM). Neither of those modules imports fastapi —
# confirmed by what they import, not assumed — so sharing just this source
# via PYTHONPATH works without ever installing the main app's dependencies.
COPY app/__init__.py /app/app/__init__.py
COPY app/core /app/app/core
COPY app/models /app/app/models
COPY mcp_server /app/mcp_server

RUN chown -R attestor:attestor /app
USER attestor

ENV PYTHONPATH=/app

CMD ["python", "-m", "mcp_server.server"]
