# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
# Build after the bot image containing the login command.
ARG BOT_IMAGE=ghcr.io/second-hand-friends/kleinanzeigen-bot:preview
FROM ${BOT_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11-utils x11vnc fluxbox novnc websockify \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=755 docker/manual-login/run.sh /opt/manual-login.sh
EXPOSE 6080
ENTRYPOINT ["/bin/bash", "/opt/manual-login.sh"]
