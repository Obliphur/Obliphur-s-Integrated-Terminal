#!/bin/sh
# coread comment notifier → webhook
#
# POSTs the comment as JSON to any HTTP endpoint (your bot bridge, ntfy,
# Slack/Discord webhook adapter, etc.).
#
# Usage:
#   COREAD_NOTIFY_CMD="/path/to/examples/notify-webhook.sh" \
#   COREAD_WEBHOOK_URL="https://example.com/hook" \
#   node server.mjs

[ -n "$COREAD_WEBHOOK_URL" ] || { echo "COREAD_WEBHOOK_URL not set" >&2; exit 1; }

# Build JSON safely (jq if available, otherwise python3)
if command -v jq >/dev/null 2>&1; then
  PAYLOAD=$(jq -n \
    --arg book_id "$COREAD_BOOK_ID" \
    --arg title "$COREAD_BOOK_TITLE" \
    --arg from "$COREAD_FROM" \
    --arg comment "$COREAD_COMMENT" \
    '{book_id: $book_id, book_title: $title, from: $from, comment: $comment}')
else
  PAYLOAD=$(python3 -c 'import json,os; print(json.dumps({
    "book_id": os.environ.get("COREAD_BOOK_ID",""),
    "book_title": os.environ.get("COREAD_BOOK_TITLE",""),
    "from": os.environ.get("COREAD_FROM",""),
    "comment": os.environ.get("COREAD_COMMENT",""),
  }))')
fi

curl -sf -X POST "$COREAD_WEBHOOK_URL" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD"
