FROM ghcr.io/astral-sh/uv:0.10.5 AS uvbin

FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DUCTOR_NO_UPDATE_CHECK=1 \
    AGY_CLI_DISABLE_AUTO_UPDATE=true \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DUCTOR_HOME=/home/node/.ductor \
    CODEX_HOME=/home/node/.codex \
    XDG_RUNTIME_DIR=/tmp/ductor-runtime \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/ductor-runtime/bus

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        dbus-daemon \
        gnome-keyring \
        gnupg \
        python3 \
        python3-pip \
        python3-venv \
        git \
        openssh-client \
        ca-certificates \
        tzdata \
        tini \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=uvbin /uv /uvx /bin/

# Copy project files (including Dockerfile.sandbox required by hatchling)
COPY pyproject.toml uv.lock README.md LICENSE config.example.json Dockerfile.sandbox ./
COPY ductor_bot ./ductor_bot

# Create venv and install project with dependencies
RUN uv venv "${VIRTUAL_ENV}" --python python3 \
    && uv pip install --python "${VIRTUAL_ENV}/bin/python" ".[api]"

# Install CLI providers
RUN npm install -g @openai/codex @anthropic-ai/claude-code @google/gemini-cli

RUN curl -fsSL https://antigravity.google/cli/install.sh -o /tmp/install-agy.sh \
    && bash /tmp/install-agy.sh --dir /usr/local/bin \
    && rm -f /tmp/install-agy.sh \
    && agy --version

COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint

RUN mkdir -p \
        /home/node/.ductor \
        /home/node/.codex \
        /home/node/.claude \
        /home/node/.gemini \
        /home/node/.local/share/keyrings \
    && chown -R node:node /home/node

USER node
WORKDIR /home/node

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/docker-entrypoint"]
CMD ["ductor"]
