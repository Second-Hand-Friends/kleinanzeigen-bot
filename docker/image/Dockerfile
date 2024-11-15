#syntax=docker/dockerfile:1.4
# see https://docs.docker.com/build/dockerfile/frontend/#dockerfile-frontend
# see https://github.com/moby/buildkit/blob/master/frontend/dockerfile/docs/reference.md#syntax
# see https://docs.docker.com/engine/reference/builder/#syntax
#
# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
#

######################
# runtime image base
######################
FROM debian:stable-slim as runtime-base-image

ARG DEBIAN_FRONTEND=noninteractive
ARG LC_ALL=C

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN <<EOF

  apt-get update

  echo "#################################################"
  echo "Installing root CAs..."
  echo "#################################################"
  apt-get install --no-install-recommends -y ca-certificates
  update-ca-certificates

  echo "#################################################"
  echo "Installing Chromium..."
  echo "#################################################"
  apt-get install --no-install-recommends -y chromium

  apt-get clean autoclean
  apt-get autoremove --purge -y
  rm -rf \
    /var/cache/{apt,debconf} \
    /var/lib/apt/lists/* \
    /var/log/{apt,alternatives.log,bootstrap.log,dpkg.log} \
    /tmp/* /var/tmp/*

EOF

######################
# build image
######################

# https://hub.docker.com/_/python/tags?name=3-slim
FROM python:3.13-slim AS build-image

ARG DEBIAN_FRONTEND=noninteractive
ARG LC_ALL=C

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN <<EOF

  apt-get update

  # install required libraries
  apt-get install --no-install-recommends -y \
    binutils `# required by pyinstaller` \
    build-essential `# required by transitive dependency "wrapt"` \
    git `# required by pdm to generate app version` \

  # upgrade pip
  python -m pip install --upgrade pip

  # install pdm
  pip install pdm

EOF

ENV PATH="/opt/upx:${PATH}"

COPY . /opt/app/

RUN <<EOF

  cd /opt/app
  ls -la .
  pdm install -v
  ls -la src/kleinanzeigen_bot
  pdm run compile
  ls -l dist

EOF

RUN /opt/app/dist/kleinanzeigen-bot --help


######################
# final image
######################
FROM runtime-base-image
COPY --from=build-image /opt/app/dist/kleinanzeigen-bot /opt/kleinanzeigen-bot

ARG BUILD_DATE
ARG GIT_COMMIT_HASH
ARG GIT_COMMIT_DATE
ARG GIT_REPO_URL

LABEL \
  maintainer="Sebastian Thomschke" \
  org.label-schema.schema-version="1.0" \
  org.label-schema.build-date=$BUILD_DATE \
  org.label-schema.vcs-ref=$GIT_COMMIT_HASH \
  org.label-schema.vcs-url=$GIT_REPO_URL \
  org.opencontainers.image.authors="Sebastian Thomschke" \
  org.opencontainers.image.vendor="Sebastian Thomschke" \
  org.opencontainers.image.licenses="AGPL-3.0" \
  org.opencontainers.image.title="kleinanzeigen-bot" \
  org.opencontainers.image.description="CLI application to ease publishing of ads to kleinanzeigen.de"

# https://stackoverflow.com/a/59812588/5116073
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=0:0

ENTRYPOINT ["/bin/bash", "/opt/run.sh"]

ENV \
  INIT_SH_FILE='' \
  CONFIG_FILE=/mnt/data/config.yaml

COPY docker/image/run.sh /opt/run.sh

VOLUME /mnt/data
