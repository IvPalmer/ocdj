#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$REPO_ROOT/tools/bin"

mkdir -p "$BIN_DIR"

say() { printf "%s\n" "$*"; }
die() { say "ERROR: $*"; exit 2; }

ensure_cmd() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1
}

install_ytdlp() {
  if [[ -x "$BIN_DIR/yt-dlp" ]]; then
    say "yt-dlp: already present at tools/bin/yt-dlp"
    return 0
  fi

if ensure_cmd yt-dlp; then
    say "yt-dlp: found in PATH, copying into tools/bin/yt-dlp"
    cp "$(command -v yt-dlp)" "$BIN_DIR/yt-dlp"
    chmod +x "$BIN_DIR/yt-dlp"
    return 0
  fi

  say "yt-dlp: not found in PATH; downloading latest macOS binary into tools/bin/yt-dlp"
  ensure_cmd curl || die "curl not found (needed to download yt-dlp)"
  curl -L --fail --retry 3 --retry-delay 2 \
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos" \
    -o "$BIN_DIR/yt-dlp"
  chmod +x "$BIN_DIR/yt-dlp"
}

install_ffmpeg() {
  local os
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"

  download_static_ffmpeg() {
    ensure_cmd curl || die "curl not found (needed to download ffmpeg)"
    local tmp
    tmp="$(mktemp -d)"
    say "ffmpeg: downloading static macOS builds into tools/bin/"
    curl -L --fail --retry 3 --retry-delay 2 \
      "https://evermeet.cx/ffmpeg/ffmpeg.zip" \
      -o "$tmp/ffmpeg.zip"
    curl -L --fail --retry 3 --retry-delay 2 \
      "https://evermeet.cx/ffmpeg/ffprobe.zip" \
      -o "$tmp/ffprobe.zip"
    /usr/bin/ditto -xk "$tmp/ffmpeg.zip" "$tmp/ffmpeg"
    /usr/bin/ditto -xk "$tmp/ffprobe.zip" "$tmp/ffprobe"
    [[ -x "$tmp/ffmpeg/ffmpeg" ]] || die "ffmpeg.zip did not contain an ffmpeg binary"
    [[ -x "$tmp/ffprobe/ffprobe" ]] || die "ffprobe.zip did not contain an ffprobe binary"
    cp "$tmp/ffmpeg/ffmpeg" "$BIN_DIR/ffmpeg"
    cp "$tmp/ffprobe/ffprobe" "$BIN_DIR/ffprobe"
    chmod +x "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"
  }

  validate_ffmpeg() {
    local ff="$1"
    local fp="$2"
    "$ff" -version >/dev/null 2>&1 && "$fp" -version >/dev/null 2>&1
  }

  if [[ -x "$BIN_DIR/ffmpeg" && -x "$BIN_DIR/ffprobe" ]]; then
    if validate_ffmpeg "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"; then
      say "ffmpeg/ffprobe: already present under tools/bin/"
      return 0
    fi
    say "ffmpeg/ffprobe: existing binaries failed to run; replacing"
    rm -f "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"
  fi

  if ensure_cmd ffmpeg && ensure_cmd ffprobe; then
    say "ffmpeg/ffprobe: found in PATH, copying into tools/bin/"
    cp "$(command -v ffmpeg)" "$BIN_DIR/ffmpeg"
    cp "$(command -v ffprobe)" "$BIN_DIR/ffprobe"
    chmod +x "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"
    if validate_ffmpeg "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"; then
      return 0
    fi
    say "ffmpeg/ffprobe: copied binaries failed to run; falling back to static download"
    rm -f "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"
  fi

  if [[ "$os" == "darwin" ]]; then
    download_static_ffmpeg
    if validate_ffmpeg "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"; then
      return 0
    fi
    say "ffmpeg/ffprobe: static download failed to run; trying Homebrew"
    rm -f "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"
  fi

  if ensure_cmd brew; then
    say "ffmpeg: not found; installing via Homebrew then copying into tools/bin/"
    brew install ffmpeg
    ensure_cmd ffmpeg || die "brew install ffmpeg finished but ffmpeg is still not in PATH"
    ensure_cmd ffprobe || die "brew install ffmpeg finished but ffprobe is still not in PATH"
    cp "$(command -v ffmpeg)" "$BIN_DIR/ffmpeg"
    cp "$(command -v ffprobe)" "$BIN_DIR/ffprobe"
    chmod +x "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"
    if validate_ffmpeg "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"; then
      return 0
    fi
    die "ffmpeg/ffprobe copied from Homebrew failed to run (missing deps?)"
  fi

  die "ffmpeg not found and Homebrew isn't installed. Install ffmpeg+ffprobe via Homebrew, then rerun this script."
}

say "Setting up media tools..."
install_ytdlp
install_ffmpeg

say
say "Validating:"
"$BIN_DIR/yt-dlp" --version
if "$BIN_DIR/ffmpeg" -version >/dev/null 2>&1; then
  "$BIN_DIR/ffmpeg" -version | head -n 1
else
  say "ffmpeg validation failed (see above)."
fi
if "$BIN_DIR/ffprobe" -version >/dev/null 2>&1; then
  "$BIN_DIR/ffprobe" -version | head -n 1
else
  say "ffprobe validation failed (see above)."
fi

say
say "Done. Binaries are in:"
say "  $BIN_DIR/yt-dlp"
say "  $BIN_DIR/ffmpeg"
say "  $BIN_DIR/ffprobe"


