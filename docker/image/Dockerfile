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
FROM debian:trixie-slim AS runtime-base-image

ARG DEBIAN_FRONTEND=noninteractive
ARG LC_ALL=C
ARG TARGETARCH

# Locales generated into the image. Override when building the image yourself, e.g.
#   bash docker/build-image.sh --build-arg LOCALES="fr_FR.UTF-8 de_DE.UTF-8"
ARG LOCALES="de_DE.UTF-8 en_US.UTF-8 en_GB.UTF-8"
ARG DEFAULT_LANG=de_DE.UTF-8
ARG DEFAULT_TZ=Europe/Berlin
ARG USER_UID=1000

# KasmVNC release assets, see https://github.com/kasmtech/KasmVNC/releases
ARG KASMVNC_VERSION=1.5.0
ARG KASMVNC_SHA256_AMD64=80b241de7dfe53bba2b7e1cc5ac8c5246d72271efa16be2d4f76607f30fab1c4
ARG KASMVNC_SHA256_ARM64=fbb11589958a2acccd2d67f67944be79ac1e8e3a1d6172c0e6db6dc59e55a919

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN <<EOF

  apt-get update

  echo "#################################################"
  echo "Installing root CAs..."
  echo "#################################################"
  apt-get install --no-install-recommends -y ca-certificates curl
  update-ca-certificates

  echo "#################################################"
  echo "Installing Chromium, fonts, locales and desktop..."
  echo "#################################################"
  apt-get install --no-install-recommends -y \
    chromium chromium-l10n chromium-sandbox \
    fonts-dejavu-core fonts-liberation fonts-noto-core fonts-noto-color-emoji fonts-noto-mono \
    libegl1 libgl1-mesa-dri libglx-mesa0 \
    locales openbox tzdata ssl-cert x11-utils

  for locale in $LOCALES; do
    sed -i "s/^# *\($locale \)/\1/" /etc/locale.gen
  done
  locale-gen

  echo "#################################################"
  echo "Installing KasmVNC $KASMVNC_VERSION ($TARGETARCH)..."
  echo "#################################################"
  case "${TARGETARCH:-amd64}" in
    amd64) kasm_arch=amd64; kasm_sha=$KASMVNC_SHA256_AMD64 ;;
    arm64) kasm_arch=arm64; kasm_sha=$KASMVNC_SHA256_ARM64 ;;
    *) echo "unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;;
  esac
  curl -fsSL -o /tmp/kasmvnc.deb \
    "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_trixie_${KASMVNC_VERSION}_${kasm_arch}.deb"
  echo "$kasm_sha  /tmp/kasmvnc.deb" | sha256sum -c -
  apt-get install --no-install-recommends -y /tmp/kasmvnc.deb
  test -x /usr/bin/Xvnc

  # KasmVNC needs a TLS key that is readable for the unprivileged user
  make-ssl-cert generate-default-snakeoil --force-overwrite
  chmod 755 /etc/ssl/private
  chmod 644 /etc/ssl/private/ssl-cert-snakeoil.key

  useradd --uid "$USER_UID" --create-home --shell /bin/bash user
  mkdir -p /mnt/data
  chown user:user /mnt/data

  apt-get purge -y curl
  apt-get clean autoclean
  apt-get autoremove --purge -y
  rm -rf \
    /var/cache/{apt,debconf} \
    /var/lib/apt/lists/* \
    /var/log/{apt,alternatives.log,bootstrap.log,dpkg.log} \
    /tmp/* /var/tmp/*

EOF

# Prefer the Noto faces so Chromium actually uses the installed fonts
COPY docker/image/fontconfig-local.conf /etc/fonts/local.conf

# Debian's chromium wrapper sources /etc/chromium.d/*: keep the sandbox whenever the
# container allows user namespaces and fall back to --no-sandbox otherwise.
COPY docker/image/chromium-sandbox.sh /etc/chromium.d/kleinanzeigen-bot-sandbox

ENV LANG=$DEFAULT_LANG \
    LANGUAGE=$DEFAULT_LANG \
    LC_ALL=$DEFAULT_LANG \
    TZ=$DEFAULT_TZ

######################
# build image
######################

# https://hub.docker.com/_/python/tags?name=3-slim
FROM python:3.12-slim AS build-image

ARG DEBIAN_FRONTEND=noninteractive
ARG LC_ALL=C
ARG GIT_COMMIT_HASH

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

EXPOSE 6080
USER user
ENTRYPOINT ["/bin/bash", "/opt/run.sh"]

ENV \
  INIT_SH_FILE='' \
  CONFIG_FILE=/mnt/data/config.yaml

COPY docker/image/run.sh /opt/run.sh

VOLUME /mnt/data
