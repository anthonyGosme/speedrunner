#!/usr/bin/env bash
# Record the demo (nightly.sh), native then through `speedrunner exec`, as
# asciinema casts: native, cold (daemons stopped, their learned caches kept,
# as for a user who ran the tools before) and warm (daemons ready). Each run
# gets a fresh local S3 (moto) to upload to.
#
# Usage: bench/demo/record.sh <speedrunner binary> <out_dir>
# Env:   RUNS (default "native cold warm"), REPEAT (default 1), NO_REC=1 for
#        logs instead of casts; ASCIINEMA, MOTO (their paths).
# Run it in a terminal: asciinema records at the terminal's size (set below).
set -euo pipefail

sr=${1:?usage: record.sh <speedrunner> <out_dir>}
out=${2:?usage: record.sh <speedrunner> <out_dir>}
here=$(cd "$(dirname "$0")" && pwd)
root=/root/speedrunner
moto=${MOTO:-$root/venv-moto/bin/moto_server}

scratch=$(mktemp -d /tmp/sr-demo-XXXXXX)
export SHOP=$scratch/shop
mkdir -p "$out" "$SHOP"
mkdir -m 0700 "$scratch/run"
python3 "$here/make_shop.py" "$SHOP" "$root/php/laravel"

export PATH=$root/venv-aws/bin:$root/venv-panel/bin:$root/gems-rb/bin:/usr/local/bin:/usr/bin:/bin
export HOME=$scratch LANG=C.UTF-8 TERM=xterm-256color PAGER=cat
export XDG_RUNTIME_DIR=$scratch/run XDG_CACHE_HOME=$scratch/cache XDG_CONFIG_HOME=$scratch/config
export GEM_HOME=$root/gems-rb GEM_PATH=$root/gems-rb
export AWS_CONFIG_FILE=$scratch/aws-config AWS_SHARED_CREDENTIALS_FILE=$scratch/aws-credentials
export AWS_EC2_METADATA_DISABLED=true
export ANSIBLE_INVENTORY=$SHOP/deploy/inventory.ini ANSIBLE_LOCAL_TEMP=$scratch/ansible
# Test-only keys for the local S3.
for profile in default dev staging prod; do
  section=$([ $profile = default ] && echo default || echo "profile $profile")
  printf '[%s]\nregion = %s\nendpoint_url = http://127.0.0.1:5055\n' "$section" \
    "$([ $profile = prod ] && echo eu-central-1 || echo eu-west-3)" >> "$AWS_CONFIG_FILE"
  printf '[%s]\naws_access_key_id = local-test\naws_secret_access_key = local-test\n' $profile \
    >> "$AWS_SHARED_CREDENTIALS_FILE"
done

moto_pid=
s3_fresh() {
  if [ -n "$moto_pid" ]; then kill "$moto_pid"; wait "$moto_pid" 2> /dev/null || true; fi
  "$moto" -H 127.0.0.1 -p 5055 > "$scratch/moto.log" 2>&1 &
  moto_pid=$!
  until aws s3 mb s3://shop-artifacts > /dev/null 2>&1; do sleep 0.2; done
}
stop_all() {
  local pid
  for pid in $(pgrep -f -- "--socket $scratch/run/" || true); do kill "$pid" 2> /dev/null || true; done
  while pgrep -f -- "--socket $scratch/run/" > /dev/null; do sleep 0.1; done
}
cleanup() {
  stop_all
  if [ -n "$moto_pid" ]; then kill "$moto_pid" 2> /dev/null || true; fi
  rm -rf "$scratch"
}
trap cleanup EXIT

# Learn, as earlier use would have; then a native run for the page cache.
s3_fresh
"$sr" exec "$here/nightly.sh" > /dev/null 2>&1
stop_all
s3_fresh
"$here/nightly.sh" > /dev/null 2>&1

# The runs: native; speedrunner with its daemons stopped (cold); and again
# with them ready, as the cold run leaves them (warm). RUNS picks some,
# REPEAT runs each that many times (casts <name>-<n>.cast).
stty cols 80 rows 32 2> /dev/null || true
repeat=${REPEAT:-1}
for name in ${RUNS:-native cold warm}; do
  case $name in
    native) cmd="$here/nightly.sh" ;;
    cold | warm) cmd="$sr exec $here/nightly.sh" ;;
  esac
  for ((k = 1; k <= repeat; k++)); do
    [ "$name" = cold ] && stop_all
    file=$out/$name.cast
    [ "$repeat" -gt 1 ] && file=$out/$name-$k.cast
    s3_fresh
    if [ "${NO_REC:-}" = 1 ]; then
      $cmd > "${file%.cast}.log" 2>&1
    else
      "${ASCIINEMA:-asciinema}" rec -q --overwrite -c "$cmd" "$file"
    fi
  done
done
