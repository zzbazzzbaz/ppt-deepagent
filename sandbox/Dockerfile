FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_PATH=/opt/pptx/node_modules \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fontconfig \
    fonts-crosextra-caladea \
    fonts-crosextra-carlito \
    fonts-liberation \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-noto-core \
    gcc \
    libreoffice-common \
    libreoffice-impress \
    nodejs \
    npm \
    poppler-utils \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.20 /uv /uvx /usr/local/bin/
COPY requirements.txt /tmp/pptx-requirements.txt
RUN uv pip install --system --no-cache -r /tmp/pptx-requirements.txt

WORKDIR /opt/pptx
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm rebuild sharp \
    && npm cache clean --force \
    && rm -rf /tmp/pptx-requirements.txt /root/.npm

COPY pptx /skills/pptx
RUN test -f /skills/pptx/SKILL.md \
    && test -f /skills/pptx/LICENSE.txt \
    && chmod -R a+rX /skills/pptx \
    && mkdir -p /workspace/input /workspace/work \
    && ln -s /opt/pptx/node_modules /workspace/node_modules

WORKDIR /workspace/work
