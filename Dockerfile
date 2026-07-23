# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build/frontend
ARG APP_VERSION=
ENV APP_VERSION=${APP_VERSION}

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ARG APP_VERSION=
ARG ODA_FILE_CONVERTER_VERSION=27.1
ARG ODA_FILE_CONVERTER_SHA256=c71363cd54758177af47a365154f180dc50a1e2b52a131994fda541c13a36766
ENV TZ=Asia/Shanghai \
    APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LIBREOFFICE_SOFFICE_PATH=/usr/bin/libreoffice \
    LIBREOFFICE_PYTHON_PATH=/usr/bin/python3 \
    ODA_CONVERTER_PATH=/usr/local/bin/oda-file-converter-headless \
    WEB_CONCURRENCY=4 \
    FORWARDED_ALLOW_IPS=*

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fontconfig \
        fonts-noto-cjk \
        libgl1 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-render-util0 \
        libxcb-render0 \
        libxcb-shape0 \
        libxcb-util1 \
        libxcb-xkb1 \
        libxkbcommon-x11-0 \
        libreoffice \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-writer \
        p7zip-full \
        poppler-utils \
        postgresql-client \
        python3-uno \
        tzdata \
        unzip \
        unrar-free \
        xauth \
        xvfb \
    && curl -fL --retry 3 \
        "https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_${ODA_FILE_CONVERTER_VERSION}.deb" \
        -o /tmp/oda-file-converter.deb \
    && echo "${ODA_FILE_CONVERTER_SHA256}  /tmp/oda-file-converter.deb" | sha256sum -c - \
    && dpkg -i /tmp/oda-file-converter.deb \
    && if [ -e /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 ] \
        && [ ! -e /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 ]; then \
        ln -s libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0; \
       fi \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -f /tmp/oda-file-converter.deb \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/oda_file_converter_headless.sh /usr/local/bin/oda-file-converter-headless
RUN chmod 0755 /usr/local/bin/oda-file-converter-headless

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY prompt_templates ./prompt_templates
COPY gunicorn.conf.py ./gunicorn.conf.py
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

RUN mkdir -p /app/data/file_records /app/data/export_tasks /app/data/import_tasks /app/logs

EXPOSE 19013

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:19013/api/health', timeout=3).read()"

# 使用 gunicorn 管理多个 UvicornWorker 进程以支撑并发；详细参数见 gunicorn.conf.py。
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
