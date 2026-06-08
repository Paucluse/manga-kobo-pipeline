FROM python:3.12-slim AS base

# System dependencies for KCC, Calibre, and archive handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Archive tools
    p7zip-full \
    unrar-free \
    # Calibre CLI (calibredb)
    calibre \
    # KCC dependencies
    libgl1 \
    libegl1 \
    && rm -rf /var/lib/apt/lists/*

# Install KCC (Kindle Comic Converter)
RUN pip install --no-cache-dir KindleComicConverter

# Create app directory
WORKDIR /app

# Copy entire project
COPY pyproject.toml README.md ./
COPY src/ src/

# Install with all extras (includes py7zr for 7z support)
RUN pip install --no-cache-dir ".[full]"

# Copy config template
COPY config.example.yaml .

# Create data directories
RUN mkdir -p /data/inbox /data/processing /data/archive_cbz \
    /data/kepub_ready /data/calibre-library /data/state \
    /data/manual-review /data/logs

# Default config path
ENV MANGA_PIPELINE_CONFIG=/app/config.yaml

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD manga-pipeline doctor || exit 1

# Default: run the pipeline in watch mode
ENTRYPOINT ["manga-pipeline"]
CMD ["run"]
