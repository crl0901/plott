# Pinned by digest so every build starts from the same image. Update it with
# `docker buildx imagetools inspect python:3.12-slim` (see docs/CONFIGURATION.md).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Optional: set BWS_VERSION and BWS_SHA256 (the SHA-256 of that version's Linux x86_64 zip) to
# install the Bitwarden Secrets Manager CLI. The build stops if the download doesn't match.
ARG BWS_VERSION=""
ARG BWS_SHA256=""
ARG APP_UID=1000
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates tzdata \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation \
 && if [ -n "$BWS_VERSION" ]; then \
      curl -fsSL -o /tmp/bws.zip \
        "https://github.com/bitwarden/sdk-sm/releases/download/bws-v${BWS_VERSION}/bws-x86_64-unknown-linux-gnu-${BWS_VERSION}.zip" \
      && echo "${BWS_SHA256}  /tmp/bws.zip" | sha256sum -c - \
      && unzip /tmp/bws.zip -d /usr/local/bin && chmod +x /usr/local/bin/bws && rm /tmp/bws.zip; \
    fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
# Every package, including dependencies, is pinned with hashes; pip refuses anything that doesn't match.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app ./app

# Claude Code CLI, used only when the board's .env has a Claude token or API key. Downloaded
# directly and checked against the SHA-256 written here (no install script). To change the version, update all three values from
# https://downloads.claude.ai/claude-code-releases/<version>/manifest.json (linux-x64, linux-arm64).
ARG CLAUDE_CODE_VERSION=2.1.118
ARG CLAUDE_CODE_SHA256_X64=ba363b2410a47120d2d4b8ece2e11fe0bbc5d59adb1329e8fb87ea0f370f4e46
ARG CLAUDE_CODE_SHA256_ARM64=b77b22fe93c15409f3c64be67950fe11e5fc17d1cd327891596cb87dd9be0492
ARG TARGETARCH
RUN case "${TARGETARCH:-amd64}" in \
      amd64) platform=linux-x64; sum="$CLAUDE_CODE_SHA256_X64" ;; \
      arm64) platform=linux-arm64; sum="$CLAUDE_CODE_SHA256_ARM64" ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/claude \
    "https://downloads.claude.ai/claude-code-releases/${CLAUDE_CODE_VERSION}/${platform}/claude" \
 && echo "${sum}  /usr/local/bin/claude" | sha256sum -c - \
 && chmod 755 /usr/local/bin/claude

RUN useradd -m -u ${APP_UID} app && mkdir -p /data && chown app /data
USER app
RUN claude --version
ENV DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CONFIG_DIR=/config DATA_DIR=/data \
    CLAUDE_CONFIG_DIR=/home/app/.claude
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
