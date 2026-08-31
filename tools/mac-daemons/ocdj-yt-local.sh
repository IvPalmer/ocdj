#!/bin/bash
# ocdj-yt-local.sh — Mac-side YouTube local-download fallback.
#
# When the VPS gets YouTube bot-checked (datacenter IP), the ytfetch job is
# parked as status 'needs_local'. This daemon (LaunchAgent, every 2 min) claims
# those jobs, downloads the audio here on the Mac's RESIDENTIAL IP — which
# YouTube doesn't bot-check — and uploads the finished file back to the VPS,
# which ingests it into the organize pipeline exactly like a VPS-side success.
#
# Config: ~/.config/ocdj/incoming.env (reused). Required keys:
#   OCDJ_API_URL=https://ocdj.grooveops.dev/api
#   KICK_TOKEN=<bearer, same as the VPS KICK_TOKEN>
#
# Prereqs on PATH: yt-dlp, ffmpeg, node (Homebrew). The plist sets PATH.

set -u
set -o pipefail

SCRIPT_NAME=$(basename "$0")
LOG_FILE="${HOME}/Library/Logs/ocdj-yt-local.log"
CONFIG_FILE="${HOME}/.config/ocdj/incoming.env"

log() {
  local ts; ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  printf '%s [%s] %s\n' "$ts" "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE" >&2
}
die() { log "FATAL: $*"; exit 1; }

[[ -f "$CONFIG_FILE" ]] || die "config missing: $CONFIG_FILE"
# shellcheck disable=SC1090
source "$CONFIG_FILE"
: "${OCDJ_API_URL:?OCDJ_API_URL not set}"
: "${KICK_TOKEN:?KICK_TOKEN not set}"

mkdir -p "$(dirname "$LOG_FILE")"
AUTH=(-H "Authorization: Bearer ${KICK_TOKEN}")

process_one() {
  local id=$1 url=$2
  log "claim id=$id url=$url"

  local workdir; workdir=$(mktemp -d "${TMPDIR:-/tmp}/ocdj-yt.XXXXXX")

  # Metadata pre-pass (residential IP, no bot-check) so the job row shows a
  # real title/artist/bitrate even though the VPS pre-pass was blocked.
  local meta vid up title abr dur ext
  # $'...' (ANSI-C quoting) so the \t become REAL tab characters — with plain
  # single quotes yt-dlp prints literal "\t" and the tab-split below fails,
  # dumping everything into $vid.
  meta=$(yt-dlp --no-playlist --skip-download \
      --print $'%(id)s\t%(uploader)s\t%(title)s\t%(abr)s\t%(duration)s\t%(ext)s' \
      -- "$url" 2>>"$LOG_FILE" | tail -1)
  IFS=$'\t' read -r vid up title abr dur ext <<< "$meta"

  # Post metadata immediately so the UI shows title/artist/bitrate while the
  # (multi-second) download runs, instead of a bare URL. Best-effort.
  curl -sS -o /dev/null "${AUTH[@]}" --max-time 20 \
      -F "video_id=${vid}" -F "uploader=${up}" -F "title=${title}" \
      -F "abr=${abr}" -F "duration=${dur}" -F "ext=${ext}" \
      "${OCDJ_API_URL}/ytfetch/${id}/meta/" 2>>"$LOG_FILE" || true

  # Same highest-quality flags as the VPS path (bestaudio -> lossless wav).
  local filepath
  filepath=$(yt-dlp --no-playlist \
      -f 'bestaudio/best' --extract-audio --audio-format wav --audio-quality 0 \
      --output "${workdir}/%(artist,creator,uploader|YouTube)s - %(title)s [%(id)s].%(ext)s" \
      --print after_move:filepath --no-progress -- "$url" 2>>"$LOG_FILE" | tail -1)

  if [[ -z "$filepath" || ! -f "$filepath" ]]; then
    log "download failed id=$id (no output file)"
    rm -rf "$workdir"
    return 1
  fi

  local base; base=$(basename "$filepath")
  log "downloaded id=$id -> $base ($(du -h "$filepath" | cut -f1)); uploading"

  local code
  code=$(curl -sS -o /tmp/ocdj-yt-deliver.out -w '%{http_code}' \
      "${AUTH[@]}" --max-time 300 \
      -F "file=@${filepath}" \
      -F "filename=${base}" \
      -F "video_id=${vid}" \
      -F "uploader=${up}" \
      -F "title=${title}" \
      -F "abr=${abr}" \
      -F "duration=${dur}" \
      -F "ext=${ext}" \
      "${OCDJ_API_URL}/ytfetch/${id}/deliver-local/" 2>>"$LOG_FILE")

  if [[ "$code" == "200" ]]; then
    log "delivered id=$id OK"
    rm -rf "$workdir"
    return 0
  fi
  log "delivery failed id=$id http=$code body=$(head -c 200 /tmp/ocdj-yt-deliver.out 2>/dev/null)"
  rm -rf "$workdir"
  return 1
}

main() {
  local resp
  resp=$(curl -sS "${AUTH[@]}" --max-time 20 "${OCDJ_API_URL}/ytfetch/pending-local/" 2>>"$LOG_FILE") \
    || die "pending-local unreachable"

  local count
  count=$(printf '%s' "$resp" | /usr/bin/python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("jobs",[])))' 2>/dev/null || echo 0)
  if [[ "$count" -eq 0 ]]; then
    exit 0  # quiet idle
  fi
  log "claimed $count job(s) for local download"

  printf '%s' "$resp" | /usr/bin/python3 -c '
import json, sys
for j in json.load(sys.stdin).get("jobs", []):
    print("\t".join([str(j["id"]), j["url"]]))
' | while IFS=$'\t' read -r id url; do
    process_one "$id" "$url" || log "job $id did not complete this cycle"
  done
}

main "$@"
