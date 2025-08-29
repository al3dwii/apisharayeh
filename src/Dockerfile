# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# OS packages: LibreOffice for PPTX->PDF, fonts (incl. Arabic), locales
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress libreoffice-writer libreoffice-calc \
    ghostscript fontconfig locales \
    fonts-dejavu-core \
    fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
    ffmpeg \
  && sed -i 's/# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen \
  && sed -i 's/# *ar_SA.UTF-8/ar_SA.UTF-8/' /etc/locale.gen \
  && locale-gen \
  && fc-cache -f -v \
  && rm -rf /var/lib/apt/lists/*


ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    SOFFICE_BIN=/usr/bin/soffice

WORKDIR /srv

# Install Python deps first (better layer caching)
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

# App code
COPY . /srv

# Defaults used by api & worker
ENV PYTHONPATH=/srv \
    PLUGINS_DIR=/srv/plugins \
    MODELS_CONFIG=/srv/config/models.yaml

EXPOSE 8080
