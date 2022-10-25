#
# Copyright (C) 2022 Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
#

######################
# runtime image base
######################
FROM debian:stable-slim as runtime-base-image

LABEL maintainer="Sebastian Thomschke"

ARG DEBIAN_FRONTEND=noninteractive
ARG LC_ALL=C

RUN set -eu \
 #
 && apt-get update -y \
 && echo "#################################################" \
 && echo "Install Chromium + Driver..." \
 && echo "#################################################" \
 && apt-get install --no-install-recommends -y chromium chromium-driver \
 #
 && rm -rf \
    /var/cache/{apt,debconf} \
    /var/lib/apt/lists/* \
    /var/log/{apt,alternatives.log,bootstrap.log,dpkg.log} \
    /tmp/* /var/tmp/*


######################
# build image
######################

# https://hub.docker.com/_/python?tab=tags&name=3-slim
FROM python:3-slim AS build-image

RUN apt-get update \
 # install required libraries
 && apt-get install --no-install-recommends -y \
    binutils `# required by pyinstaller` \
    build-essential `# required by transitive dependency "wrapt"` \
    git `# required by pdm to generate app version` \
 #
 # upgrade pip
 && python -m pip install --upgrade pip \
 #
 # install pdm
 && pip install pdm

ENV PATH="/opt/upx:${PATH}"

COPY kleinanzeigen_bot /opt/app/kleinanzeigen_bot
COPY .git /opt/app/.git
COPY README.md pdm.lock pyinstaller.spec pyproject.toml /opt/app/

RUN cd /opt/app \
 && ls -la . \
 && pdm install -v \
 && ls -la kleinanzeigen_bot \
 && pdm run compile \
 && ls -l dist

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
  org.label-schema.schema-version="1.0" \
  org.label-schema.build-date=$BUILD_DATE \
  org.label-schema.vcs-ref=$GIT_COMMIT_HASH \
  org.label-schema.vcs-url=$GIT_REPO_URL

# https://stackoverflow.com/a/59812588/5116073
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=0:0

ENTRYPOINT ["/bin/bash", "/opt/run.sh"]

ENV \
  INIT_SH_FILE='' \
  CONFIG_FILE=/mnt/data/config.yaml

COPY docker/image/run.sh /opt/run.sh

VOLUME /mnt/data
