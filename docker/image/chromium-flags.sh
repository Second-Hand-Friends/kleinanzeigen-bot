# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

# Sourced by Debian's /usr/bin/chromium wrapper, which resets CHROMIUM_FLAGS
# before sourcing /etc/chromium.d/*, so flags must be added here.

# Chromium's sandbox needs user namespaces, which many container runtimes block,
# and it refuses to start as root at all.
if [ "$(id -u)" = 0 ] || ! unshare --user true 2>/dev/null; then
  export CHROMIUM_FLAGS="${CHROMIUM_FLAGS:-} --no-sandbox"
fi

# flags chosen by run.sh at container start (e.g. --enable-unsafe-swiftshader without a GPU)
if [ -n "${KLEINANZEIGEN_BOT_CHROMIUM_FLAGS:-}" ]; then
  export CHROMIUM_FLAGS="${CHROMIUM_FLAGS:-} $KLEINANZEIGEN_BOT_CHROMIUM_FLAGS"
fi
