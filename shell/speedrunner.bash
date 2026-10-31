# speedrunner for bash (>= 4.2): commands run through their tool's preloaded
# daemon, and the daemon is warmed up while the command line is typed.
#
#   eval "$(speedrunner init bash)"    # in ~/.bashrc (this file, built into the client)
#   srr aws s3 ls                      # any tool, nothing to set up
#   speedrunner enroll aws             # or once, then plain `aws s3 ls`
#
# Enrolled tools are listed in ${XDG_CONFIG_HOME:-~/.config}/speedrunner/tools.

declare -gA _speedrunner_tools=()    # enrolled tool -> 1
declare -gA _speedrunner_wrapped=()  # tool -> the wrapper we defined, as bash prints it
declare -gi _speedrunner_warmed=0    # set once the current line warmed a daemon

_speedrunner_load() {
  local file=${XDG_CONFIG_HOME:-$HOME/.config}/speedrunner/tools tool
  _speedrunner_tools=()
  [[ -r $file ]] || return 0
  while IFS= read -r tool || [[ -n $tool ]]; do
    [[ $tool =~ ^[[:alnum:]._][[:alnum:]._-]*$ ]] || continue
    _speedrunner_tools[$tool]=1
    # Leave the user's own aliases and functions alone.
    alias "$tool" > /dev/null 2>&1 && continue
    if declare -F "$tool" > /dev/null && [[ $(declare -f "$tool") != "${_speedrunner_wrapped[$tool]-}" ]]; then
      continue
    fi
    eval "$tool() { command speedrunner run $(printf %q "$tool") -- \"\$@\"; }"
    _speedrunner_wrapped[$tool]=$(declare -f "$tool")
  done < "$file"
}

# Reload the wrappers right after `speedrunner enroll`.
speedrunner() {
  command speedrunner "$@"
  local rc=$?
  (( rc == 0 )) && [[ ${1-} == enroll ]] && _speedrunner_load
  return $rc
}

# First in PROMPT_COMMAND: keeps $? for what follows (prompts that show it).
_speedrunner_line_init() {
  local rc=$?
  _speedrunner_warmed=0
  return $rc
}

# Bound to the space key: inserts the space, then, once per line, warms up
# the daemon of the command word it completes. Pure parameter expansion;
# leading VAR=value assignments are skipped. After the `srr` prefix, the
# tool that follows is warmed up without being enrolled.
_speedrunner_space() {
  READLINE_LINE="${READLINE_LINE:0:READLINE_POINT} ${READLINE_LINE:READLINE_POINT}"
  (( READLINE_POINT += 1 ))
  (( _speedrunner_warmed )) && return
  local rest=${READLINE_LINE#"${READLINE_LINE%%[![:space:]]*}"} word prefixed=0
  while :; do
    [[ $rest == *[[:space:]]* ]] || return
    word=${rest%%[[:space:]]*}
    rest=${rest#*[[:space:]]}
    rest=${rest#"${rest%%[![:space:]]*}"}
    [[ $word =~ ^[[:alpha:]_][[:alnum:]_]*= ]] && continue
    if (( ! prefixed )) && [[ $word == srr ]]; then
      prefixed=1
      continue
    fi
    break
  done
  [[ $word =~ ^[[:alnum:]._][[:alnum:]._-]*$ ]] || return
  (( prefixed )) || [[ -n ${_speedrunner_tools[$word]+x} ]] || return
  _speedrunner_warmed=1
  (command speedrunner prewarm "$word" < /dev/null > /dev/null 2>&1 &)
}

_speedrunner_load
if [[ $- == *i* ]]; then
  [[ ${PROMPT_COMMAND-} == *_speedrunner_line_init* ]] \
    || PROMPT_COMMAND="_speedrunner_line_init${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
  # Only where space still inserts a space: a binding of the user's
  # (magic-space…) is left alone, and only warm-up is lost.
  for _speedrunner_map in emacs vi-insert; do
    if [[ $(bind -m "$_speedrunner_map" -p 2> /dev/null | grep -F '" ": ') == '" ": self-insert' ]]; then
      bind -m "$_speedrunner_map" -x '" ": _speedrunner_space'
    fi
  done
  unset _speedrunner_map
fi
