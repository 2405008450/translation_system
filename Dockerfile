# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LIBREOFFICE_SOFFICE_PATH=/usr/bin/libreoffice \
    LIBREOFFICE_PYTHON_PATH=/usr/bin/python3 \
    WEB_CONCURRENCY=4 \
    FORWARDED_ALLOW_IPS=*

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fontconfig \
        fonts-noto-cjk \
        libreoffice \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-writer \
        p7zip-full \
        python3-uno \
        tzdata \
        unzip \
        unrar-free \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY prompt_templates ./prompt_templates
COPY gunicorn.conf.py ./gunicorn.conf.py
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

RUN mkdir -p /app/data/file_records /app/data/export_tasks /app/logs

EXPOSE 19013

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:19013/api/health', timeout=3).read()"

# 使用 gunicorn 管理多个 UvicornWorker 进程以支撑并发；详细参数见 gunicorn.conf.py。
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
