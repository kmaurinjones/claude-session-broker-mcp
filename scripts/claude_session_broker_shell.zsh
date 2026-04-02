typeset -gr CLAUDE_SESSION_BROKER_SHELL_DIR="${${(%):-%N}:A:h}"

function claude() {
  local tty_name
  local broker_data_dir
  local recorder_script
  tty_name="$(ps -o tty= -p $$ | tr -d '[:space:]')"
  broker_data_dir="${CLAUDE_SESSION_BROKER_DATA_DIR:-${CC_BROKER_DATA_DIR:-$HOME/.claude-session-broker}}"
  recorder_script="${CLAUDE_SESSION_BROKER_SHELL_DIR}/record_claude_launch.py"

  if [[ -n "$tty_name" && "$tty_name" != "?" && "$tty_name" != "??" ]]; then
    if [[ -n "${CC_NAME:-}" && -n "${CC_GROUP:-}" ]]; then
      /usr/bin/python3 \
        "$recorder_script" \
        --tty "$tty_name" \
        --cwd "$PWD" \
        --data-dir "$broker_data_dir" \
        --session-name "$CC_NAME" \
        --group-name "$CC_GROUP" \
        --description "${CC_DESCRIPTION:-}"
    else
      /usr/bin/python3 \
        "$recorder_script" \
        --tty "$tty_name" \
        --cwd "$PWD" \
        --data-dir "$broker_data_dir" \
        --clear
    fi
  fi

  command claude "$@"
}
