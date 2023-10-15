#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

set -e -u

##############################
# execute script with bash if loaded with other shell interpreter
##############################
if [ -z "${BASH_VERSINFO:-}" ]; then /usr/bin/env bash "$0" "$@"; exit; fi

set -o pipefail

trap 'echo >&2 "$(date +%H:%M:%S) Error - exited with status $? at line $LINENO:"; pr -tn $0 | tail -n+$((LINENO - 3)) | head -n7' ERR

if [ -f "$INIT_SH_FILE" ]; then
   source "$INIT_SH_FILE"
fi

cd /mnt/data

/opt/kleinanzeigen-bot --config $CONFIG_FILE "$@"
