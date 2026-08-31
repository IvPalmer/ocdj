#!/bin/bash
# ocdj-incoming.sh — reverse-drain: push locally-dropped tracks INTO the VPS
# ocdj pipeline.
#
# Watches an incoming folder on this Mac for audio files, rsyncs them to the
# VPS's 01_downloaded/ stage, then kicks off processing. Moves uploaded files
# to _uploaded/ inside the incoming dir so they're not re-uploaded.
#
# Invoked by a user LaunchAgent every 2 minutes (see
# dev.grooveops.ocdj-incoming.plist). Runs as the user so we can read files in
# ~/Music/ without privilege tricks.
#
# Config lives in ~/.config/ocdj/incoming.env. Required keys:
#   INCOMING_DIR=/Users/palmer/Music/_incoming
#   VPS_SSH=ubuntu@main-instance
#   VPS_INBOX=/srv/ocdj/pipeline/01_downloaded
#   OCDJ_API_URL=https://ocdj.grooveops.dev/api
#   KICK_TOKEN=<bearer-token, same value as VPS KICK_TOKEN env>
#
# KICK_TOKEN authenticates the pipeline/kick/ POST (in-app bearer check,
# organize/auth.py::require_kick_token) — the Traefik ocdj-kick router only
# gets the request past the reverse proxy, it does not authenticate it.

set -u
set -o pipefail

SCRIPT_NAME=$(basename "$0")
LOG_FILE="${HOME}/Library/Logs/ocdj-incoming.log"
CONFIG_FILE="${HOME}/.config/ocdj/incoming.env"

log() {
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  printf '%s [%s] %s\n' "$ts" "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE" >&2
}

die() { log "FATAL: $*"; exit 1; }

# ─── Load config ─────────────────────────────────────────────────────────
if [[ ! -f "$CONFIG_FILE" ]]; then
  die "config missing: $CONFIG_FILE — see scripts/ocdj-incoming/incoming.env.example"
fi
# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${INCOMING_DIR:?INCOMING_DIR not set}"
: "${VPS_SSH:?VPS_SSH not set}"
: "${VPS_INBOX:?VPS_INBOX not set}"
: "${OCDJ_API_URL:?OCDJ_API_URL not set}"
: "${KICK_TOKEN:?KICK_TOKEN not set in $CONFIG_FILE}"

UPLOADED_DIR="${INCOMING_DIR}/_uploaded"
FAILED_DIR="${INCOMING_DIR}/_failed"
mkdir -p "$INCOMING_DIR" "$UPLOADED_DIR" "$FAILED_DIR" "$(dirname "$LOG_FILE")"

AUDIO_EXTS="mp3|flac|wav|aiff|aif|m4a|ogg"

# ─── Preflight ────────────────────────────────────────────────────────────
preflight() {
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$VPS_SSH" true 2>/dev/null; then
    die "SSH to $VPS_SSH failed"
  fi
  if ! ssh -o BatchMode=yes "$VPS_SSH" test -d "$VPS_INBOX"; then
    die "VPS inbox missing: $VPS_INBOX"
  fi
}

# ─── Main loop ────────────────────────────────────────────────────────────
main() {
  preflight

  # Skip dotfiles, anything in the _uploaded/_failed subdirs, and obvious non-audio.
  # Recurses: Soulseek and most rips hand you an album *folder*, and a
  # depth-1 sweep ignored those silently — files sat here forever.
  local -a candidates=()
  while IFS= read -r -d '' f; do
    candidates+=("$f")
  done < <(find "$INCOMING_DIR" -type f \
              \( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.wav" \
                 -o -iname "*.aiff" -o -iname "*.aif" -o -iname "*.m4a" -o -iname "*.ogg" \) \
              -not -path "${UPLOADED_DIR}/*" -not -path "${FAILED_DIR}/*" \
              -print0 2>/dev/null)

  if [[ ${#candidates[@]} -eq 0 ]]; then
    # Quiet idle run — just log occasionally (every 10th run via time-based mod) so the log isn't spammed.
    local minute
    minute=$(date -u +"%M")
    if [[ $(( 10#$minute % 10 )) -eq 0 ]]; then
      log "idle (no audio files in $INCOMING_DIR)"
    fi
    return 0
  fi

  log "cycle: ${#candidates[@]} file(s) to upload"
  local uploaded=0

  for src in "${candidates[@]}"; do
    local base remote_target
    base=$(basename "$src")
    remote_target="${VPS_INBOX}/${base}"

    # Refuse to overwrite files already present on VPS.
    if ssh -o BatchMode=yes "$VPS_SSH" test -e "'$remote_target'"; then
      log "skip: remote already has '$base' — moving local to _failed/"
      mv -f "$src" "${FAILED_DIR}/$base" 2>&1 || true
      continue
    fi

    # Rsync into the VPS inbox. --partial keeps resumable state if killed.
    if rsync -az --partial --timeout=300 \
        "$src" "${VPS_SSH}:${VPS_INBOX}/"; then
      # Fix ownership to ubuntu so backend container (root-in-container reading
      # the bind mount) can manipulate the file just like slskd-written ones.
      ssh -o BatchMode=yes "$VPS_SSH" "chmod 644 '${remote_target}'" 2>/dev/null || true
      mv -f "$src" "${UPLOADED_DIR}/$base"
      log "uploaded: $base"
      uploaded=$((uploaded + 1))
    else
      log "rsync failed for $base — leaving in incoming for retry"
    fi
  done

  # A dropped album folder is left as an empty shell once its audio is
  # uploaded; clear those so the drop zone doesn't accumulate them. Only
  # genuinely empty dirs go — anything still holding a file is left alone.
  find "$INCOMING_DIR" -mindepth 1 -type d \
      -not -path "$UPLOADED_DIR" -not -path "${UPLOADED_DIR}/*" \
      -not -path "$FAILED_DIR" -not -path "${FAILED_DIR}/*" \
      -empty -delete 2>/dev/null || true

  if [[ $uploaded -gt 0 ]]; then
    local resp
    resp=$(curl -sS --max-time 20 -X POST \
           -H 'Content-Type: application/json' \
           -H "Authorization: Bearer ${KICK_TOKEN}" \
           "${OCDJ_API_URL}/organize/pipeline/kick/" 2>&1)
    log "kicked pipeline ($uploaded newly uploaded): $resp"
  fi
}

main "$@"
