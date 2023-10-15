#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

set -eu

#################################################
# execute script with bash if loaded with other shell interpreter
#################################################
if [ -z "${BASH_VERSINFO:-}" ]; then /usr/bin/env bash "$0" "$@"; exit; fi

set -o pipefail


#################################################
# configure error reporting
#################################################
trap 'rc=$?; echo >&2 "$(date +%H:%M:%S) Error - exited with status $rc in [$BASH_SOURCE] at line $LINENO:"; cat -n $BASH_SOURCE | tail -n+$((LINENO - 3)) | head -n7' ERR


#################################################
# determine directory of current script
#################################################
this_file_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")"; pwd -P)
project_root=$(cd "$this_file_dir/.."; pwd -P)
docker_file="$this_file_dir/image/Dockerfile"
echo "project_root=$project_root"


#################################################
# use .gitignore as .dockerignore
#################################################
cp -f "$project_root/.gitignore" "$project_root/.dockerignore"


#################################################
# specify target docker registry/repo
#################################################
image_repo=second-hand-friends/kleinanzeigen-bot
image_name=$image_repo:latest


#################################################
# build the image
#################################################
echo "Building docker image [$image_name] from [$project_root]..."
docker image pull python:3-slim || true # ensure we have the latest version of the base image

if [[ $OSTYPE == "cygwin" || $OSTYPE == "msys" ]]; then
   project_root=$(cygpath -w "$project_root")
   docker_file=$(cygpath -w "$docker_file")
fi

docker build "$project_root" \
   --file "$docker_file" \
   --progress=plain \
   --build-arg BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ") \
   --build-arg GIT_COMMIT_DATE="$(date -d @$(git log -1 --format='%at') --utc +'%Y-%m-%d %H:%M:%S UTC')" \
   --build-arg GIT_COMMIT_HASH="$(git rev-parse --short HEAD)" \
   --build-arg GIT_REPO_URL="$(git config --get remote.origin.url)" \
   -t $image_name \
   "$@"
