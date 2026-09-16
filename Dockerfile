FROM python:3.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        aria2 \
        curl \
        ca-certificates \
        unzip \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL=/root/.deno
ENV PATH="$DENO_INSTALL/bin:$PATH"

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ src/
COPY start.sh ./
RUN chmod +x start.sh

RUN mkdir -p downloads user_cookies thumbnails

ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

CMD ["./start.sh"]
