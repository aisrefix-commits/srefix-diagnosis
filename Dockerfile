FROM python:3.11-slim

WORKDIR /app

COPY explorer-mcp/ ./explorer-mcp/

RUN pip install --no-cache-dir -e ./explorer-mcp

ENTRYPOINT ["srefix-explorer"]
