FROM python:3.12-slim

# Optional: set BWS_VERSION (for example 2.1.0) to install the Bitwarden Secrets Manager CLI.
ARG BWS_VERSION=""
ARG CLAUDE_CODE_VERSION=2.1.118
ARG APP_UID=1000
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates tzdata \
    libpango-1.0-0 libpangoft2-1.0-0 fonts-liberation \
 && if [ -n "$BWS_VERSION" ]; then \
      curl -fsSL -o /tmp/bws.zip \
        "https://github.com/bitwarden/sdk-sm/releases/download/bws-v${BWS_VERSION}/bws-x86_64-unknown-linux-gnu-${BWS_VERSION}.zip" \
      && unzip /tmp/bws.zip -d /usr/local/bin && chmod +x /usr/local/bin/bws && rm /tmp/bws.zip; \
    fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN useradd -m -u ${APP_UID} app && mkdir -p /data && chown app /data
USER app
# Claude Code CLI. Used only when the board's .env has a Claude token or API key.
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_VERSION} \
 && /home/app/.local/bin/claude --version
ENV PATH=/home/app/.local/bin:$PATH DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1 CONFIG_DIR=/config DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
