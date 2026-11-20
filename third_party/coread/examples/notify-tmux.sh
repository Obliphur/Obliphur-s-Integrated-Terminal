#!/bin/sh
# coread comment notifier → tmux
#
# Injects the human's comment into a tmux session running your AI agent
# (e.g. Claude Code), so the AI sees it as an incoming message in real time.
#
# Usage:
#   COREAD_NOTIFY_CMD="/path/to/examples/notify-tmux.sh" \
#   COREAD_TMUX_SESSION="main" \
#   node server.mjs
#
# coread passes comment details via env vars:
#   COREAD_BOOK_ID, COREAD_BOOK_TITLE, COREAD_FROM, COREAD_COMMENT

SESSION="${COREAD_TMUX_SESSION:-main}"

MSG="[coread] ${COREAD_FROM} commented on 《${COREAD_BOOK_TITLE}》: ${COREAD_COMMENT}"

tmux send-keys -t "$SESSION" -l "$MSG" || exit 1
sleep 0.3
tmux send-keys -t "$SESSION" Enter
