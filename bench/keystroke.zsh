#!/usr/bin/env zsh
# Stage 3 bench: cost of the zle-line-pre-redraw hook, which runs on every
# keystroke. Calls the widget function directly, outside zle.
#
# Usage: zsh bench/keystroke.zsh
zmodload zsh/datetime
source ${0:A:h}/../shell/speedrunner.zsh
_speedrunner_tools=(aws 1)

per_call() {  # label, buffer, warmed, calls
  local label=$1 n=$4 t0 i
  BUFFER=$2
  t0=$EPOCHREALTIME
  for (( i = 0; i < n; i++ )); do
    _speedrunner_warmed=$3
    _speedrunner_pre_redraw
  done
  printf '%-48s %7.2f µs/keystroke\n' $label $(( (EPOCHREALTIME - t0) * 1e6 / n ))
}

_noop() { }
t0=$EPOCHREALTIME
for (( i = 0; i < 20000; i++ )); do _speedrunner_warmed=0; _noop; done
printf '%-48s %7.2f µs/keystroke\n' "baseline: empty function, same loop" $(( (EPOCHREALTIME - t0) * 1e6 / 20000 ))
per_call "first word not complete: 'aw'" "aw" 0 20000
per_call "another command: 'ls -la /tmp/some/dir'" "ls -la /tmp/some/dir" 0 20000
per_call "assignments, another command" "A=1 B=2 ls -la /tmp" 0 20000
per_call "line already warmed (long line)" "aws ec2 describe-instances --region eu-west-3 --output json" 1 20000

# The one call per line that fires the prewarm: zsh pays a fork, not the
# prewarm itself (&!). A no-op stands in for speedrunner.
stub=$(mktemp -d)
print '#!/bin/sh' > $stub/speedrunner && chmod +x $stub/speedrunner
path=($stub $path)
per_call "enrolled tool, fires the background prewarm" "aws " 0 200
rm -rf $stub
