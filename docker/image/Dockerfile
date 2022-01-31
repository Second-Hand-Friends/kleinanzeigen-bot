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
    #curl xz-utils `# required to install upx` \
    git `# required by pdm to generate app version` \
 #
 # install upx
 # upx is currently not supported on Linux, see https://github.com/pyinstaller/pyinstaller/discussions/6275
 #&& mkdir /opt/upx \
 #&& upx_download_url=$(curl -fsSL https://api.github.com/repos/upx/upx/releases/latest | grep browser_download_url | grep amd64_linux.tar.xz | cut "-d\"" -f4) \
 #&& echo "Downloading [$upx_download_url]..." \
 #&& curl -fL $upx_download_url | tar Jxv -C /opt/upx --strip-components=1 \
 #
 # upgrade pip
 # don't upgrade PIP for now: https://github.com/pdm-project/pdm/issues/874
 #&& python -m pip install --upgrade pip \
 #
 # install pdm
 && pip install pdm

ENV PATH="/opt/upx:${PATH}"

COPY kleinanzeigen_bot /opt/app/kleinanzeigen_bot
COPY .git /opt/app/.git
COPY README.md pdm.lock pyinstaller.spec pyproject.toml /opt/app/

RUN cd /opt/app \
 && ls -la . \
 # https://github.com/SeleniumHQ/selenium/issues/10022 / https://github.com/pdm-project/pdm/issues/728#issuecomment-1021771200
 && pip install -t __pypackages__/3.10/lib selenium \
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
