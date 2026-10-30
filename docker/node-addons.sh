#!/usr/bin/env bash
# The Node daemon's addon (server/speedrunner_node.c), built for glibc and for
# musl, as client/build.rs embeds them (SPEEDRUNNER_NODE_ADDONS=<dir>): glibc
# on Ubuntu 22.04, so that it loads on any glibc from 2.35 up; musl on Alpine.
#
#   docker/node-addons.sh <out_dir> [platform]     (platform: linux/amd64, ...)
set -euo pipefail
cd "$(dirname "$0")/.."
out=$1
platform=${2:-$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')}
mkdir -p "$out"
flags="-O2 -Wall -shared -fPIC"
docker run --rm --platform "$platform" -v "$PWD/server:/s:ro" -v "$(cd "$out" && pwd):/out" ubuntu:22.04 sh -c \
  "apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends gcc libc6-dev >/dev/null \
   && gcc $flags -o /out/speedrunner_node-gnu.node /s/speedrunner_node.c"
docker run --rm --platform "$platform" -v "$PWD/server:/s:ro" -v "$(cd "$out" && pwd):/out" alpine:3.20 sh -c \
  "apk add -q gcc musl-dev && gcc $flags -o /out/speedrunner_node-musl.node /s/speedrunner_node.c"
ls -l "$out"
