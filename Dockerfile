# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    wl-clipboard \
    xclip \
    xsel \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --user --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /root/.local /root/.local


# Set PATH to include local pip install directory
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_LOGGER_LEVEL=info

Copy my_agent/  /app/

# Create .streamlit config directory
RUN mkdir -p ~/.streamlit

# Create Streamlit config
RUN echo "[server]\n\
maxUploadSize=200\n\
enableXsrfProtection=false\n\
\n\
[client]\n\
showErrorDetails=false\n\
" > ~/.streamlit/config.toml

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8080/_stcore/health')" || exit 1

# Run Streamlit
CMD ["adk web", "run", "app.py", "--server.port=8080", "--logger.level=info", "--theme.base=light"]