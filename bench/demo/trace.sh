# Sourced by the demo script: bash's own trace (set -x) shows each command
# as it runs, with its real arguments, after the elapsed time and a prompt.

_t0=${EPOCHREALTIME/./}
# Expanded before each traced command: arithmetic only, no fork. Times are
# rounded to the tenth, as the video's clocks are.
PS4=$'\e[2m''$(( _t = (${EPOCHREALTIME/./} - _t0 + 50000) / 100000, _t / 10 )).$(( _t % 10 ))s'$'\e[0m \e[1;32m❯\e[0m '
BASH_XTRACEFD=1  # in order with the tools' own output

# The script's last line: stop tracing, print the total.
finish() {
  local t=$(( (${EPOCHREALTIME/./} - _t0 + 50000) / 100000 ))
  printf '\n\e[1;32m✔\e[0m \e[1mnightly checks done in %d.%d s\e[0m\n\n' $((t / 10)) $((t % 10))
}
set -x
