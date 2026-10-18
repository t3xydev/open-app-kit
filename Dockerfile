FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    ANDROID_HOME=/opt/android-sdk \
    PATH="/opt/android-sdk/build-tools/36.0.0:${PATH}" \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

# bookworm-backports: newer androguard (fixes "res1 must be zero!" on modern APKs)
RUN echo "deb http://deb.debian.org/debian bookworm-backports main" \
      > /etc/apt/sources.list.d/backports.list \
    && apt-get update && apt-get install -y --no-install-recommends \
      fdroidserver \
      rclone \
      wget \
      unzip \
      openjdk-17-jre-headless \
      ca-certificates \
      python3 \
      python3-pip \
      python3-venv \
      openssl \
      bash \
    && apt-get install -y --no-install-recommends -t bookworm-backports \
      androguard \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p "$ANDROID_HOME/build-tools" \
    && wget -q -O /tmp/bt.zip \
       "https://dl.google.com/android/repository/build-tools_r36-linux.zip" \
    && unzip -q /tmp/bt.zip -d "$ANDROID_HOME/build-tools" \
    && mv "$ANDROID_HOME/build-tools/android-14" "$ANDROID_HOME/build-tools/36.0.0" 2>/dev/null || true \
    && rm /tmp/bt.zip \
    && chmod +x "$ANDROID_HOME/build-tools/36.0.0/"* 2>/dev/null || true

WORKDIR /srv/fdroid

COPY backend/requirements.txt backend/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages \
      -r backend/requirements.txt

COPY scripts/ scripts/
COPY backend/ backend/
COPY assets/ assets/
COPY .env.example .env.example

RUN chmod +x scripts/*.sh \
    && mkdir -p /data

ENV PORT=8000 \
    DATA_DIR=/data

EXPOSE 8000

# Same entrypoint for Docker Compose, plain Docker, and Railway
CMD ["scripts/start.sh"]
