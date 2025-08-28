# infra/worker.Dockerfile
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOFFICE_BIN=/usr/bin/soffice

# OS deps: LibreOffice (headless), FFmpeg, and fonts (Noto families incl. Arabic)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice \
      libreoffice-impress \
      ffmpeg \
      fontconfig \
      fonts-noto-core \
      fonts-noto-extra \
      fonts-noto-cjk \
      fonts-noto-color-emoji \
      fonts-dejavu-core \
      locales \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Locale (helps rendering consistency)
RUN sed -i 's/# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

WORKDIR /app
# (Optional) If you plan to run the worker process from this image later:
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt
# COPY src ./src

# Quick smoke entrypoint to inspect runtime (override in compose/CI)
CMD bash -lc 'echo "soffice: $(soffice --headless --version 2>&1 | head -n1)"; echo "ffmpeg: $(ffmpeg -version 2>&1 | head -n1)"; echo "Fonts sample:"; fc-list | grep -E "Noto|DejaVu" | head -n 20 || true'
