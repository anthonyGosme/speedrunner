#!/bin/bash
# ansible's temp directory, made at load, shared by every served call.
# Usage: ansible_temp_dir.sh <speedrunner binary> <directory holding ansible>
sr=$1
home=$(mktemp -d /tmp/sr-ansible-XXXXXX)
export PATH=$2:/usr/bin:/bin HOME=$home LANG=C.UTF-8 XDG_RUNTIME_DIR=$home/run
mkdir -m 700 $home/run
cd "$home" || exit 1
echo 'hello {{ 1 + 1 }}' > t.j2
at=$home/.ansible/tmp

calls() {  # label, command prefix...
  local label=$1; shift
  for args in "ping" "copy -a content=hello\ dest=c.txt" "template -a src=t.j2\ dest=t.txt"; do
    local codes=""
    for _ in 1 2 3; do
      eval "$* localhost -c local -m $args" > out.txt 2>&1
      codes="$codes $?"
    done
    echo "$label ${args%% *}: exit codes$codes"
    [ "${codes##* }" = 0 ] || grep -m1 '"msg"' out.txt | sed "s|$home|<home>|g"
  done
}

ansible --version > /dev/null
calls plain ansible
echo "--- temp directories after the plain calls: $(ls "$at" 2> /dev/null | wc -l)"

for _ in 1 2 3 4; do "$sr" run ansible -- --version > /dev/null; sleep 1; done
sleep 2
"$sr" status ansible > /dev/null
daemon=$("$sr" status ansible | sed -n 's/.*pid=\([0-9]*\).*/\1/p')
echo "--- daemon warm, its pid: $daemon. Temp directories:"
ls "$at"
calls served "SPEEDRUNNER_REQUIRE_FAST=1 $sr run ansible --"
echo "--- temp directories after the served calls:"
ls "$at"
"$sr" stop ansible > /dev/null
sleep 2
echo "--- after stopping the daemon:"
ls "$at"
rm -rf "$home"
