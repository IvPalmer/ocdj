#!/bin/bash
# FLAC → AIFF, bit-perfect. macOS. Double-click me.
#
# AIFF is uncompressed big-endian PCM and FLAC is compressed PCM, so a correct
# conversion changes the container and nothing else — same samples, same rate,
# same bit depth. "Maximum quality" here is not a setting to turn up; it is the
# absence of everything that would degrade it:
#
#   * the PCM codec is chosen to match the source bit depth. Writing a 24-bit
#     master as pcm_s16be silently throws away the low 8 bits, and that is the
#     usual way this conversion goes wrong.
#   * no -ar, so the sample rate is never resampled.
#   * no -ac, so channels are never mixed.
#   * no dither, no normalisation, no filters of any kind.
#
# Every file is then verified: both the source and the result are decoded to
# identical raw PCM and hashed. Same hash = the audio is untouched. That check
# is why this script can promise bit-perfect rather than assert it.

set -uo pipefail

# Deliberately no cd: the folder picker returns absolute paths, and changing
# directory here would break any relative path passed as an argument.

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'
YELLOW=$'\033[33m'; RESET=$'\033[0m'

echo "${BOLD}FLAC → AIFF${RESET}  ${DIM}(sem perda, verificado arquivo por arquivo)${RESET}"
echo

# ── ffmpeg ───────────────────────────────────────────────────
# Homebrew installs outside the PATH a double-clicked script inherits, so look
# in the usual places before giving up.
for p in /opt/homebrew/bin /usr/local/bin /opt/local/bin; do
  [ -x "$p/ffmpeg" ] && PATH="$p:$PATH"
done

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "${RED}ffmpeg não encontrado.${RESET}"
  echo
  echo "Instale com uma destas opções e rode este script de novo:"
  echo
  echo "  ${BOLD}1.${RESET} Homebrew (recomendado) — cole no Terminal:"
  echo "     /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  echo "     brew install ffmpeg"
  echo
  echo "  ${BOLD}2.${RESET} Baixe pronto em https://evermeet.cx/ffmpeg/ e coloque"
  echo "     o programa 'ffmpeg' em /usr/local/bin"
  echo
  read -r -p "Enter para fechar."
  exit 1
fi

# ── What to convert ──────────────────────────────────────────
# Arguments when run from a terminal; otherwise a native folder picker, which
# is the only part of this that a double-click can't answer on its own.
if [ "$#" -gt 0 ]; then
  TARGETS=("$@")
else
  PICKED=$(osascript -e 'try
    POSIX path of (choose folder with prompt "Escolha a pasta com os arquivos FLAC")
  end try' 2>/dev/null)
  if [ -z "$PICKED" ]; then
    echo "Nada escolhido."
    read -r -p "Enter para fechar."
    exit 0
  fi
  TARGETS=("$PICKED")
fi

# ── Collect the files ────────────────────────────────────────
FILES=()
for t in "${TARGETS[@]}"; do
  t="${t%/}"
  if [ -d "$t" ]; then
    while IFS= read -r -d '' f; do FILES+=("$f"); done \
      < <(find "$t" -type f \( -iname '*.flac' \) -print0 2>/dev/null)
  elif [ -f "$t" ]; then
    case "$t" in *.flac|*.FLAC|*.Flac) FILES+=("$t");; esac
  fi
done

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "${YELLOW}Nenhum arquivo .flac encontrado.${RESET}"
  read -r -p "Enter para fechar."
  exit 0
fi

echo "${#FILES[@]} arquivo(s) para converter."
echo

ok=0; failed=0; skipped=0

# Raw PCM at a fixed width, so the comparison is sensitive to bit depth. A
# hash of the decoded stream in its own format would match even after the
# low bits were thrown away, which makes it useless as a check.
audio_hash() {
  ffmpeg -v error -i "$1" -map 0:a -c:a pcm_s32le -f hash -hash md5 - 2>/dev/null
}

for src in "${FILES[@]}"; do
  out="${src%.*}.aiff"
  name="$(basename "$src")"

  if [ -e "$out" ]; then
    echo "${DIM}pulado (já existe): $name${RESET}"
    skipped=$((skipped + 1))
    continue
  fi

  depth=$(ffprobe -v error -select_streams a:0 \
          -show_entries stream=bits_per_raw_sample -of csv=p=0 "$src" 2>/dev/null)
  fmt=$(ffprobe -v error -select_streams a:0 \
        -show_entries stream=sample_fmt -of csv=p=0 "$src" 2>/dev/null)

  # bits_per_raw_sample is the truth for FLAC; sample_fmt only says how the
  # decoder hands the samples over (24-bit arrives as s32).
  case "$depth" in
    8)  codec=pcm_s8 ;;
    16) codec=pcm_s16be ;;
    24) codec=pcm_s24be ;;
    32) codec=pcm_s32be ;;
    *)  case "$fmt" in
          s16*) codec=pcm_s16be ;;
          s32*) codec=pcm_s32be ;;
          *)    codec=pcm_s24be ;;   # widen rather than truncate when unsure
        esac ;;
  esac

  printf '%s' "convertendo (${depth:-?}-bit): $name … "

  # -map 0 keeps cover art; -c:v copy passes it through untouched. Tags come
  # over as ID3, which is what Rekordbox and Serato read from AIFF.
  if ffmpeg -v error -y -i "$src" -map 0 -c:a "$codec" -c:v copy \
       -map_metadata 0 -write_id3v2 1 "$out" 2>/tmp/f2a_err.txt; then
    :
  else
    # Some FLACs carry artwork the AIFF muxer refuses. Audio matters more.
    if ffmpeg -v error -y -i "$src" -map 0:a -c:a "$codec" \
         -map_metadata 0 -write_id3v2 1 "$out" 2>/tmp/f2a_err.txt; then
      :
    else
      echo "${RED}erro${RESET}"
      sed 's/^/    /' /tmp/f2a_err.txt | head -3
      failed=$((failed + 1))
      rm -f "$out"
      continue
    fi
  fi

  if [ "$(audio_hash "$src")" = "$(audio_hash "$out")" ]; then
    echo "${GREEN}ok, idêntico${RESET}"
    ok=$((ok + 1))
  else
    echo "${RED}ÁUDIO DIFERENTE — arquivo removido${RESET}"
    rm -f "$out"
    failed=$((failed + 1))
  fi
done

echo
echo "${BOLD}Pronto.${RESET} ${GREEN}$ok convertido(s)${RESET}, $skipped pulado(s), ${RED}$failed com erro${RESET}."
echo "${DIM}Os .flac originais não foram tocados.${RESET}"
echo
read -r -p "Enter para fechar."
