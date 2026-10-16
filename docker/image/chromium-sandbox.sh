# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

# Sourced by Debian's /usr/bin/chromium wrapper.
# Chromium's sandbox needs user namespaces, which many container runtimes block.
if ! unshare --user true 2>/dev/null; then
  export CHROMIUM_FLAGS="${CHROMIUM_FLAGS:-} --no-sandbox"
fi
