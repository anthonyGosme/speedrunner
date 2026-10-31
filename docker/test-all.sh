#!/usr/bin/env bash
# The regression suites across distributions and language versions, in
# Docker, in parallel, with a summary table. For a laptop (Docker Desktop)
# or CI. Run from the repository root:
#
#   docker/test-all.sh                  # everything
#   docker/test-all.sh alpine py3.9     # some targets (names as in the table)
#   docker/test-all.sh node24 node24-alpine   # the Node suite
#   JOBS=4 docker/test-all.sh           # at most 4 containers at a time
#   SUITES="ruby php" docker/test-all.sh fedora       # only these suites
#   PLATFORM=linux/arm64 docker/test-all.sh alpine   # another architecture
#                                       # (emulated by Docker Desktop: slow)
#
# The client is built once, static (musl), as releases are, and mounted in
# every container. Logs: docker/logs/<platform>/<target>.log (git-ignored).
set -uo pipefail
cd "$(dirname "$0")/.."
jobs=${JOBS:-$(( $(getconf _NPROCESSORS_ONLN) / 2 > 0 ? $(getconf _NPROCESSORS_ONLN) / 2 : 1 ))}
# Always explicit: a base image pulled for another platform keeps its tag,
# and would then be used, emulated (QEMU has no ptrace), for "native" runs.
PLATFORM=${PLATFORM:-$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')}
logs=docker/logs/$(echo "$PLATFORM" | tr / -)  # docker/logs/linux-arm64
mkdir -p "$logs"

# target | dockerfile | build args | run options | suites
TARGETS=(
  "debian|docker/Dockerfile|||suites matrix shell scripts ruby php"
  "ubuntu-24.04|docker/Dockerfile.ubuntu|UBUNTU=24.04||suites matrix shell scripts ruby php"
  "ubuntu-22.04|docker/Dockerfile.ubuntu|UBUNTU=22.04||suites matrix shell scripts ruby php"
  "alpine|docker/Dockerfile.alpine|||suites matrix shell scripts ruby php"
  "fedora|docker/Dockerfile.fedora|||suites matrix shell scripts ruby php"
  "non-root|docker/Dockerfile.ubuntu|UBUNTU=24.04|--user 1000:1000 -e HOME=/tmp|suites matrix scripts ruby php"
  "no-ptrace|docker/Dockerfile.ubuntu|UBUNTU=24.04|--security-opt seccomp=docker/no-ptrace.json|suites matrix"
)
for v in 3.9 3.10 3.11 3.12 3.13 3.14; do TARGETS+=("py$v|docker/lang/python.Dockerfile|VERSION=$v||suites matrix scripts"); done
# Ruby 3.0: its official image is on Debian 11, whose archive no longer
# serves it; Ubuntu 22.04 above runs the Ruby suite on 3.0.
for v in 3.1 3.2 3.3 3.4; do TARGETS+=("rb$v|docker/lang/ruby.Dockerfile|VERSION=$v||suites ruby"); done
for v in 8.1 8.2 8.3 8.4; do TARGETS+=("php$v|docker/lang/php.Dockerfile|VERSION=$v||suites php"); done
# Node: module.registerHooks, which learning needs, is in 22.15 and later.
for v in 22 24; do TARGETS+=("node$v|docker/lang/node.Dockerfile|VERSION=$v-bookworm-slim||suites node"); done
TARGETS+=("node24-alpine|docker/lang/node.Dockerfile|VERSION=24-alpine||suites node")  # the musl addon
# Races: parallel calls while daemons restart, with a terminal as CI has.
TARGETS+=("stress|docker/Dockerfile||-t|suites stress")
TARGETS+=("stress-node|docker/lang/node.Dockerfile|VERSION=24-bookworm-slim|-t|suites stress")

wanted() {
  [ $# -eq 0 ] && return 0
  local t; for t in "$@"; do [ "$t" = "$name" ] && return 0; done; return 1
}

platform="--platform $PLATFORM"
arch=$PLATFORM
echo "building the Node addons and the static client ($arch)"
bin=client/target/docker-${arch//\//-}/speedrunner
addons=client/target/node-addons-${arch//\//-}
mkdir -p "$(dirname "$bin")"
docker/node-addons.sh "$addons" "$PLATFORM" > /dev/null || { echo "addon build failed"; exit 1; }
# From a copy: a newer cargo would rewrite the repository's Cargo.lock.
docker run --rm $platform -e OUT="/src/$bin" -e SPEEDRUNNER_NODE_ADDONS="/src/$addons" -v "$PWD:/src" rust:1-alpine sh -c \
  'apk add -q musl-dev && mkdir /tmp/r && cp -r /src/client /src/server /src/shell /tmp/r \
   && rm -rf /tmp/r/client/target && cd /tmp/r/client \
   && cargo build -q --locked --release && cp target/release/speedrunner "$OUT"' \
  || { echo "static build failed"; exit 1; }

run_target() {
  local name=$1 file=$2 args=$3 opts=$4 cmd=${SUITES:+suites $SUITES} log=$logs/$1.log
  local image=speedrunner-test-$1-${arch//\//-}
  cmd=${cmd:-$5}
  local build=(docker build -q $platform -t "$image" -f "$file")
  [ -n "${STRESS_SECONDS:-}" ] && opts="$opts -e STRESS_SECONDS=$STRESS_SECONDS"
  [ -n "$args" ] && build+=(--build-arg "$args")
  {
    echo "\$ ${build[*]} docker"
    "${build[@]}" docker && \
    docker run --rm $platform $opts -v "$PWD:/src" -v "$PWD/$bin:/opt/speedrunner:ro" \
      -e SPEEDRUNNER_BIN=/opt/speedrunner "$image" $cmd
  } > "$log" 2>&1
  echo $? > "$log.status"
}

selected=()
for t in "${TARGETS[@]}"; do
  IFS='|' read -r name file args opts cmd <<< "$t"
  wanted "$@" && selected+=("$t")
done
echo "${#selected[@]} targets, $jobs at a time; logs in $logs/"
for t in "${selected[@]}"; do
  IFS='|' read -r name file args opts cmd <<< "$t"
  # No `wait -n`: macOS ships bash 3.2.
  while [ "$(jobs -rp | wc -l)" -ge "$jobs" ]; do sleep 1; done
  rm -f "$logs/$name.log.status"
  run_target "$name" "$file" "$args" "$opts" "$cmd" &
done
wait

# One row per target: each suite's result, from its "== suite" block.
failed=0
printf "%-14s %-8s %-8s %-8s %-8s %-8s %-8s %-8s\n" target matrix shell scripts ruby php node stress
for t in "${selected[@]}"; do
  IFS='|' read -r name _ <<< "$t"
  log=$logs/$name.log
  row=$(python3 - "$log" << 'PY'
import re, sys
text = open(sys.argv[1], errors="replace").read()
cells = []
for suite in ("matrix", "shell", "scripts", "ruby", "php", "node", "stress"):
    m = re.search(rf"^== {suite}\n(.*?)(?=^== |\Z)", text, re.S | re.M)
    if not m:
        cells.append("-")
        continue
    body = m.group(1)
    ran = re.search(r"Ran (\d+) tests?", body)
    ok = re.search(r"^OK", body, re.M)
    cells.append(f"{'ok' if ok else 'FAIL'}:{ran.group(1) if ran else '?'}")
print(" ".join(f"{c:<8}" for c in cells))
PY
)
  status=$(cat "$log.status" 2>/dev/null || echo "?")
  [ "$status" = 0 ] || failed=1
  printf "%-14s %s%s\n" "$name" "$row" "$([ "$status" = 0 ] || echo "  (exit $status, see $log)")"
done
# In CI, the logs of what failed, where the job's own log shows them.
if [ -n "${CI:-}" ]; then
  for t in "${selected[@]}"; do
    IFS='|' read -r name _ <<< "$t"
    if [ "$(cat "$logs/$name.log.status" 2>/dev/null)" != 0 ]; then
      echo; echo "######## $name"; cat "$logs/$name.log"
    fi
  done
fi
exit $failed
