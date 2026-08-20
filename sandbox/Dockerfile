FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS node-runtime

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
    poppler-utils \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY --from=ghcr.io/astral-sh/uv:0.9.20 /uv /uvx /usr/local/bin/
COPY requirements.txt /tmp/pptx-requirements.txt
RUN uv pip install --system --no-cache -r /tmp/pptx-requirements.txt

WORKDIR /opt/pptx
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm rebuild sharp \
    && npm cache clean --force \
    && rm -rf /tmp/pptx-requirements.txt /root/.npm

RUN mkdir -p /mnt/mounts/skills /mnt/mounts/input /mnt/mounts/work /mnt/mounts/output \
    && ln -s /mnt/mounts/skills /skills \
    && ln -s /mnt/mounts/input /workspace/input \
    && ln -s /mnt/mounts/work /workspace/work \
    && ln -s /mnt/mounts/output /workspace/output \
    && ln -s /opt/pptx/node_modules /workspace/node_modules

WORKDIR /workspace/work
