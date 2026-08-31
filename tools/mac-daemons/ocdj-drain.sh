#!/bin/bash
# ocdj-drain.sh — Mac-side drain daemon.
#
# Fetches publishable tracks from ocdj.grooveops.dev, rsyncs from VPS, verifies
# sha256, idempotently adds to Music.app via osascript, confirms with the API
# so the VPS can delete its copy.
#
# Invoked by a user LaunchAgent every 5 minutes (see dev.grooveops.ocdj-drain.plist).
# Must run as the logged-in GUI user — AppleEvents to Music require a user session.
#
# Config lives in ~/.config/ocdj/drain.env. Required keys:
#   DRAIN_API_URL=https://ocdj.grooveops.dev/api/drain
#   DRAIN_TOKEN=<bearer-token>
#   VPS_SSH=ubuntu@main-instance
#   VPS_PUBLISH_ROOT=/srv/ocdj/publish
#   STAGING_DIR=/Users/palmer/Music/ocdj-staging
#   MUSIC_IMPORT_DIR (optional, default: ~/Music/iTunes/iTunes Media/Music/ocdj)
#     Where the daemon MOVES files before telling Music.app to import.
#     Music.app's `add POSIX file` AppleScript call ignores the
#     "Copy files to Music Media folder" pref — it just references. So we
#     pre-place the file inside the Media folder ourselves, and Music.app
#     imports a reference to a file that's already in its managed area.
#
# Exits non-zero on preflight failure so launchd logs the failure loudly.

set -u
set -o pipefail

SCRIPT_NAME=$(basename "$0")
LOG_FILE="${HOME}/Library/Logs/ocdj-drain.log"
CONFIG_FILE="${HOME}/.config/ocdj/drain.env"

log() {
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  printf '%s [%s] %s\n' "$ts" "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE" >&2
}

die() {
  log "FATAL: $*"
  exit 1
}

# ─── Load config ─────────────────────────────────────────────────────────
if [[ ! -f "$CONFIG_FILE" ]]; then
  die "config file missing: $CONFIG_FILE — see scripts/ocdj-drain/drain.env.example"
fi
# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${DRAIN_API_URL:?DRAIN_API_URL not set in $CONFIG_FILE}"
: "${DRAIN_TOKEN:?DRAIN_TOKEN not set in $CONFIG_FILE}"
: "${VPS_SSH:?VPS_SSH not set in $CONFIG_FILE}"
: "${VPS_PUBLISH_ROOT:?VPS_PUBLISH_ROOT not set in $CONFIG_FILE}"
: "${STAGING_DIR:?STAGING_DIR not set in $CONFIG_FILE}"
: "${MUSIC_IMPORT_DIR:="$HOME/Music/Musicas/Electronic/_Review"}"

mkdir -p "$STAGING_DIR" "$MUSIC_IMPORT_DIR" "$(dirname "$LOG_FILE")"

# ─── Preflight ────────────────────────────────────────────────────────────
preflight() {
  # Music.app "copy on add" pref doesn't apply to scripted adds — the daemon
  # pre-places files in MUSIC_IMPORT_DIR instead. No pref check needed.

  # 1. osascript can reach Music (also launches it if not running).
  if ! osascript -e 'tell application "Music" to version' >/dev/null 2>&1; then
    die "osascript cannot talk to Music.app (grant Automation permission in System Settings → Privacy → Automation)"
  fi

  # 2. API reachable + token valid.
  local health_code
  health_code=$(curl -fsS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${DRAIN_TOKEN}" \
    "${DRAIN_API_URL}/health/" \
    --max-time 10 || echo "000")
  if [[ "$health_code" != "200" ]]; then
    die "drain health endpoint returned $health_code — check DRAIN_API_URL / DRAIN_TOKEN / CF Access"
  fi

  # 3. Staging + music-import dirs writable.
  if ! touch "${STAGING_DIR}/.writecheck" 2>/dev/null; then
    die "cannot write to staging dir: $STAGING_DIR"
  fi
  rm -f "${STAGING_DIR}/.writecheck"
  if ! touch "${MUSIC_IMPORT_DIR}/.writecheck" 2>/dev/null; then
    die "cannot write to music import dir: $MUSIC_IMPORT_DIR"
  fi
  rm -f "${MUSIC_IMPORT_DIR}/.writecheck"

  # 4. SSH to VPS works (BatchMode = no password prompt).
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$VPS_SSH" true 2>/dev/null; then
    die "SSH to $VPS_SSH failed — check ~/.ssh/config"
  fi
}

# ─── Music.app queries via osascript ──────────────────────────────────────
music_query_existing() {
  # Returns persistent ID if a track with grouping "ocdj:<id>" already in library.
  # Empty string otherwise.
  local id=$1
  osascript 2>/dev/null <<APPLESCRIPT
tell application "Music"
  try
    set t to first track of library playlist 1 whose grouping is "ocdj:${id}"
    return persistent ID of t
  on error
    return ""
  end try
end tell
APPLESCRIPT
}

music_add_file() {
  # Adds a file, waits for Music to ingest, returns persistent ID via grouping re-query.
  local id=$1
  local path=$2
  osascript 2>/dev/null <<APPLESCRIPT
tell application "Music"
  if not running then launch
  with timeout of 120 seconds
    add POSIX file "${path}"
  end timeout
  -- Music copies async when 'Copy files to Media folder' is ON. Poll for up to 60s.
  set waitedFor to 0
  repeat while waitedFor < 60
    try
      set t to first track of library playlist 1 whose grouping is "ocdj:${id}"
      return persistent ID of t
    on error
      delay 2
      set waitedFor to waitedFor + 2
    end try
  end repeat
  return ""
end tell
APPLESCRIPT
}

# ─── Per-track drain ──────────────────────────────────────────────────────
# The claim token proves this confirmation belongs to the artifact we actually
# downloaded. Without it a late confirm can archive a track the operator has
# since re-tagged, deleting the newer bytes. Older backends ignore the extra
# key, so this daemon can be updated ahead of them.
api_fail() {
  local id=$1
  local reason=$2
  local claim_token=${3-}   # default: a missed arg logs a retryable 409, not a fatal set -u abort
  curl -fsS -X POST \
    -H "Authorization: Bearer ${DRAIN_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$(printf '{"reason":"%s","claim_token":"%s"}' "${reason//\"/\'}" "$claim_token")" \
    --max-time 10 \
    "${DRAIN_API_URL}/${id}/fail/" >/dev/null 2>&1 || true
}

api_confirm() {
  local id=$1
  local persistent_id=$2
  local claim_token=${3-}   # default: a missed arg logs a retryable 409, not a fatal set -u abort
  curl -fsS -X POST \
    -H "Authorization: Bearer ${DRAIN_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$(printf '{"music_persistent_id":"%s","claim_token":"%s"}' "$persistent_id" "$claim_token")" \
    --max-time 15 \
    "${DRAIN_API_URL}/${id}/confirm/" >/dev/null 2>&1
}

drain_one() {
  local id=$1
  local filename=$2
  local sha=$3
  local work_path=$4
  local claim_token=$5

  log "drain: starting id=$id sha=${sha:0:12} filename=$filename"

  local staging_track_dir="${STAGING_DIR}/${id}"
  mkdir -p "$staging_track_dir"

  # Remote publish dir is /srv/ocdj/publish/<id>/ — pull the single audio file.
  # No -z: these are AIFF and FLAC, already incompressible, so compression buys
  # nothing and costs throughput. Measured on a 144 MB AIFF from this VPS:
  # -az 16.5 MB/s, -a 37 MB/s.
  if ! rsync -a --timeout=120 \
      "${VPS_SSH}:${VPS_PUBLISH_ROOT}/${id}/" \
      "${staging_track_dir}/" >>"$LOG_FILE" 2>&1; then
    log "drain: rsync failed id=$id"
    api_fail "$id" "rsync failed" "$claim_token"
    rm -rf "$staging_track_dir"
    return 1
  fi

  local local_path="${staging_track_dir}/${filename}"
  if [[ ! -f "$local_path" ]]; then
    log "drain: staged file missing id=$id expected=$local_path"
    api_fail "$id" "staged file missing after rsync" "$claim_token"
    rm -rf "$staging_track_dir"
    return 1
  fi

  # Integrity verify on the staging copy before moving to Media folder.
  local local_sha
  local_sha=$(shasum -a 256 "$local_path" | awk '{print $1}')
  if [[ "$local_sha" != "$sha" ]]; then
    log "drain: sha256 mismatch id=$id expected=$sha got=$local_sha"
    api_fail "$id" "sha256 mismatch" "$claim_token"
    rm -rf "$staging_track_dir"
    return 1
  fi

  # Land the file in MUSIC_IMPORT_DIR (the user's _Review triage folder).
  # DO NOT add to Music.app — iTunes only receives tracks the user has
  # manually promoted to the real library (~/Music/Musicas/Electronic/).
  # The _Review folder is a staging area; adding from here would pollute
  # iTunes Match + any Rekordbox/Traktor syncs that watch the library.
  local import_path="${MUSIC_IMPORT_DIR}/${filename}"
  if [[ -e "$import_path" ]]; then
    log "drain: target already exists, refusing overwrite id=$id path=$import_path"
    api_fail "$id" "target already exists in MUSIC_IMPORT_DIR" "$claim_token"
    rm -rf "$staging_track_dir"
    return 1
  fi
  if ! mv "$local_path" "$import_path" 2>&1; then
    log "drain: mv to MUSIC_IMPORT_DIR failed id=$id"
    api_fail "$id" "mv to import dir failed" "$claim_token"
    rm -rf "$staging_track_dir"
    return 1
  fi
  log "drain: landed in _Review id=$id path=$import_path (no Music.app add)"
  # Placeholder persistent_id — backend schema still expects it as proof of
  # delivery; use a synthetic marker so VPS can archive the row.
  local persistent_id="_REVIEW_LANDED"

  # Confirm with VPS — it deletes its copy on success.
  if ! api_confirm "$id" "$persistent_id" "$claim_token"; then
    log "drain: confirm API failed id=$id — will retry next cycle"
    # Do NOT mark fail; we added to Music already. Next cycle's idempotent query
    # will find it and re-confirm without duplicating.
    return 1
  fi

  log "drain: DONE id=$id"
  rm -rf "$staging_track_dir"
  return 0
}

# ─── Main ────────────────────────────────────────────────────────────────
main() {
  preflight
  log "drain: cycle start"

  local response
  response=$(curl -fsS \
    -H "Authorization: Bearer ${DRAIN_TOKEN}" \
    --max-time 10 \
    "${DRAIN_API_URL}/publishable/?limit=10" 2>&1)
  if [[ $? -ne 0 ]]; then
    die "drain API unreachable: $response"
  fi

  local count
  count=$(printf '%s' "$response" | /usr/bin/python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("count",0))')
  if [[ "$count" -eq 0 ]]; then
    log "drain: nothing publishable"
    exit 0
  fi

  log "drain: claimed $count track(s)"

  # Emit one line per track, tab-separated: id / sha / filename / work_path /
  # claim_token. The token goes LAST and defaults to empty: tab is IFS
  # whitespace, so an empty field in the middle would collapse and shift every
  # later field. Trailing-empty is safe, which keeps this daemon working
  # against a backend that predates claim tokens.
  printf '%s' "$response" | /usr/bin/python3 -c '
import json, sys
d = json.load(sys.stdin)
for it in d.get("items", []):
    print("\t".join([str(it["id"]), it["sha256"], it["filename"], it["work_path"], it.get("claim_token", "")]))
' | while IFS=$'\t' read -r id sha filename work_path claim_token; do
    if ! drain_one "$id" "$filename" "$sha" "$work_path" "$claim_token"; then
      log "drain: item $id did not complete this cycle"
    fi
  done

  log "drain: cycle end"
}

main "$@"
