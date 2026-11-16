#!/bin/bash
# Each command natively, then served (SPEEDRUNNER_REQUIRE_FAST): stdout+stderr and exit code compared.
# A descriptor above 2 inherited from the caller would send every call native: closed first.
for fd in /dev/fd/*; do fd=${fd##*/}; [ "$fd" -gt 2 ] 2>/dev/null && eval "exec $fd>&-" 2>/dev/null; done
B=$1; shift
for c in "$@"; do
  read -r -a args <<< "$c"
  n=$(brew "${args[@]}" 2>&1; echo "rc=$?")
  f=$(SPEEDRUNNER_REQUIRE_FAST=1 "$B" run brew -- "${args[@]}" 2>&1; echo "rc=$?")
  if [ "$n" = "$f" ]; then echo "same: brew $c"; else echo "DIFF: brew $c"; diff <(echo "$n") <(echo "$f") | head -6; fi
done
