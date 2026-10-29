# speedrunner for zsh (>= 5.3): commands run through their tool's preloaded
# daemon, and the daemon is warmed up while the command line is typed.
#
#   eval "$(speedrunner init zsh)"     # in ~/.zshrc (this file, built into the client)
#   srr aws s3 ls                   # any tool, nothing to set up
#   speedrunner enroll aws             # or once, then plain `aws s3 ls`
#
# Enrolled tools are listed in ${XDG_CONFIG_HOME:-~/.config}/speedrunner/tools.

typeset -gA _speedrunner_tools    # enrolled tool -> 1
typeset -gA _speedrunner_wrapped  # tool -> body of the wrapper we defined
typeset -gi _speedrunner_warmed   # set once the current line warmed a daemon

_speedrunner_load() {
  emulate -L zsh -o extended_glob
  local file=${XDG_CONFIG_HOME:-$HOME/.config}/speedrunner/tools tool
  _speedrunner_tools=()
  [[ -r $file ]] || return 0
  for tool in ${(f)"$(<$file)"}; do
    [[ $tool == [[:alnum:]._][[:alnum:]._-]# ]] || continue
    _speedrunner_tools[$tool]=1
    # Leave the user's own aliases and functions alone.
    (( $+aliases[$tool] )) && continue
    (( $+functions[$tool] )) && [[ $functions[$tool] != $_speedrunner_wrapped[$tool] ]] && continue
    functions[$tool]="command speedrunner run ${(q)tool} -- \"\$@\""
    _speedrunner_wrapped[$tool]=$functions[$tool]  # as zsh normalised it
  done
}

# Reload the wrappers right after `speedrunner enroll`.
speedrunner() {
  command speedrunner "$@"
  local rc=$?
  (( rc == 0 )) && [[ $1 == enroll ]] && _speedrunner_load
  return rc
}

_speedrunner_line_init() {
  _speedrunner_warmed=0
}

# Runs before every redraw, so on every keystroke: pure parameter expansion,
# and at most one background prewarm per line, once the command word is
# complete. Leading VAR=value assignments are skipped. After the `srr`
# prefix, the tool that follows is warmed up without being enrolled.
_speedrunner_pre_redraw() {
  # The common keystrokes first, before paying for emulate.
  (( _speedrunner_warmed )) && return
  [[ $BUFFER == *[[:space:]]* ]] || return
  emulate -L zsh -o extended_glob
  local rest=${BUFFER##[[:space:]]#} word
  local -i prefixed=0
  while :; do
    [[ $rest == *[[:space:]]* ]] || return
    word=${rest%%[[:space:]]*}
    rest=${${rest#*[[:space:]]}##[[:space:]]#}
    [[ $word == [[:alpha:]_][[:alnum:]_]#=* ]] && continue
    if (( ! prefixed )) && [[ $word == srr ]]; then
      prefixed=1
      continue
    fi
    break
  done
  [[ $word == [[:alnum:]._][[:alnum:]._-]# ]] || return
  (( prefixed || $+_speedrunner_tools[$word] )) || return
  _speedrunner_warmed=1
  command speedrunner prewarm $word </dev/null >/dev/null 2>&1 &!
}

_speedrunner_load
if [[ -o interactive ]]; then
  # Not loaded yet while .zshrc runs, and add-zle-hook-widget quietly gives up without it.
  zmodload zsh/zle
  autoload -Uz add-zle-hook-widget
  add-zle-hook-widget zle-line-init _speedrunner_line_init
  add-zle-hook-widget zle-line-pre-redraw _speedrunner_pre_redraw
fi
