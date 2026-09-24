# syntax=docker/dockerfile:1

# uv wird nur während des Builds benötigt und funktioniert auf arm64/amd64.
FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# dsk und der optionale Cloudflare-Bypass benötigen diese Werkzeuge.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        build-essential \
        chromium \
        xvfb \
    && ln -sf /usr/bin/chromium /usr/bin/google-chrome \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Zuerst nur die selten veränderten Dependency-Dateien kopieren, damit Docker
# den teuren Installationsschritt cachen kann.
COPY requirements.txt /tmp/requirements.txt
COPY docker/patch_dsk_api.py /tmp/patch_dsk_api.py
RUN uv venv "${VIRTUAL_ENV}" \
    && uv pip install --python "${VIRTUAL_ENV}/bin/python" -r /tmp/requirements.txt

# Das Upstream-Projekt ist source-only und wird deshalb direkt in das Image
# gelegt, statt als pip-VCS-Paket installiert zu werden. Der Build patcht nur
# den Stream-Parser: Die aktuelle DeepSeek-Web-App sendet JSON-Patch-SSE-Events,
# während die alte Originalversion nur choices[].delta versteht.
RUN mkdir -p /app/vendor \
    && git clone --depth 1 \
        https://github.com/xtekky/deepseek4free.git \
        /app/vendor/deepseek4free \
    && python /tmp/patch_dsk_api.py \
        /app/vendor/deepseek4free/dsk/api.py \
    && uv pip install \
        --python "${VIRTUAL_ENV}/bin/python" \
        -r /app/vendor/deepseek4free/requirements.txt \
    && uv pip install \
        --python "${VIRTUAL_ENV}/bin/python" \
        fastapi uvicorn pyvirtualdisplay requests

COPY bot.py /app/bot.py
COPY docker/get_cookies.py /app/docker/get_cookies.py

# Kein Root-Prozess im laufenden Container. UID 1000 passt normalerweise zum
# ersten Raspberry-Pi-Benutzer und zum data-Bind-Mount in docker-compose.yml.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app /opt/venv

USER bot
WORKDIR /app

CMD ["python", "bot.py"]
