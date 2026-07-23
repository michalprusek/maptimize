# Hosted remote MCP connector (Streamable HTTP). Pure-Python, no GPU.
FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
COPY maptalk_mcp ./maptalk_mcp
RUN pip install --no-cache-dir .

# Non-root.
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 8080
ENV MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8080 \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "maptalk_mcp", "--transport", "http"]
