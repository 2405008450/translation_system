# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build/frontend
ARG APP_VERSION=
ENV APP_VERSION=${APP_VERSION}

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

WORKDIR /build/psd_runtime
COPY psd_runtime/package*.json ./
RUN npm ci --omit=dev --ignore-scripts


FROM python:3.11-slim-bookworm AS runtime

ARG APP_VERSION=
ENV TZ=Asia/Shanghai \
    APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LIBREOFFICE_SOFFICE_PATH=/usr/bin/libreoffice \
    LIBREOFFICE_PYTHON_PATH=/usr/bin/python3 \
    PSD_NODE_PATH=/usr/local/bin/node \
    PSD_BRIDGE_PATH=/app/psd_runtime/bridge.cjs \
    PSD_PROCESS_TIMEOUT_SECONDS=120 \
    PSD_NODE_MAX_OLD_SPACE_MB=512 \
    PSD_IMAGEMAGICK_PATH=/usr/bin/convert \
    PSD_IMAGEMAGICK_TIMEOUT_SECONDS=90 \
    PSD_OCR_DEVICE=cpu \
    PADDLE_PDX_CACHE_HOME=/opt/paddlex \
    PADDLE_PDX_MODEL_SOURCE=BOS \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False \
    WEB_CONCURRENCY=2 \
    FORWARDED_ALLOW_IPS=*

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fontconfig \
        fonts-noto-cjk \
        libgl1 \
        libglib2.0-0 \
        libstdc++6 \
        libreoffice \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-writer \
        imagemagick \
        p7zip-full \
        postgresql-client \
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

# 在镜像构建阶段下载并初始化 PP-OCRv6；运行容器无需联网获取模型。
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, enable_mkldnn=False, device='cpu')"

COPY app ./app
COPY scripts ./scripts
COPY prompt_templates ./prompt_templates
COPY gunicorn.conf.py ./gunicorn.conf.py
COPY psd_runtime/*.cjs ./psd_runtime/
COPY psd_runtime/package*.json ./psd_runtime/
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /build/psd_runtime/node_modules ./psd_runtime/node_modules
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

RUN mkdir -p /app/data/file_records /app/data/export_tasks /app/data/import_tasks /app/logs

EXPOSE 19013

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:19013/api/health', timeout=3).read()"

# 使用 gunicorn 管理多个 UvicornWorker 进程以支撑并发；详细参数见 gunicorn.conf.py。
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
