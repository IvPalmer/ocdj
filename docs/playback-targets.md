# Playback targets — what the decks actually accept

The library exists to be played on Pioneer CDJs. Every format decision in the
pipeline is downstream of what the deck loads, so this file records the real
hardware constraints and the evidence behind them.

## The deck

**Pioneer CDJ-900**, two of them, with a Pioneer 4-channel mixer. Verified from
a photo of the live setup on 2026-08-14 — the model is printed on the deck, and
the front panel lists the supported formats:

```
MP3 / AAC / WAV / AIFF
```

AIFF is supported natively. The historical worry that "the CDJ won't read AIFF"
does not apply to this deck.

## Sample rate is the real limit

| rate | CDJ-900 |
|------|---------|
| 44.1 kHz | yes |
| 48 kHz | yes |
| 88.2 / 96 kHz | **no** — nothing before the CDJ-3000 loads these |

Bit depth (16 vs 24) is not the problem on this generation. Mono files play
fine; they just come out of both channels.

### The pipeline used to emit unplayable files

`converter.py` builds `ffmpeg -i src -c:a pcm_s24be -y dst` with no `-ar`, so
**ffmpeg passed the source sample rate straight through**. A 96 kHz FLAC became
a 96 kHz AIFF that converted cleanly, imported cleanly, and failed only when
loaded on the deck — the worst kind of failure, because nothing upstream
reports an error.

This produced 8 unplayable files in the live library, found on 2026-08-14:

```
Ghost - Eye Wont Stop                     Perception - Green Vindaloo
Stone Cold Steppaz - Hit 'Em (Vocal Mix)  Shorterz & Lopaski - Letting Go
Urban Myths - I Just Can't Help           D Base - Base Theory
Herb LF - Underground 2002                Large Joints - What Have You Done Lately
```

They were converted to 44.1 kHz by hand and the pipeline now clamps at the
source: `_sample_rate_args()` adds `-ar 48000` only when the source exceeds
`MAX_SAMPLE_RATE`. Files already at 44.1 or 48 kHz are passed through
bit-exact — nothing that already worked gets resampled.

## USB sticks

The CDJ-900 generation reads **FAT16 / FAT32 / HFS+**. It does **not** read
exFAT, and the partition scheme must be MBR.

A trap worth knowing: `diskutil list` reports an exFAT volume as
`Windows_NTFS`, because exFAT reuses partition type `0x07`. Trust
`diskutil info`'s *File System Personality*, not the partition type.

```
diskutil eraseDisk FAT32 <NAME> MBRFormat /dev/diskN
```

The volume label must be 11 characters or fewer.

### Filenames

FAT32 rejects `\ / : * ? " < > |`. This is enforced by the filesystem, not by
convention — verified by attempting to create `teste Party Time?.mp3` on a
freshly formatted FAT32 stick, which fails with `no such file or directory`.

A `?` in a **title tag** is fine. A `?` in a **filename** is not, and it will
only surface when the file is copied to the stick.

Renaming a file that rekordbox already knows about breaks its database link —
the export then fails with `[1] The file doesn't exist` and the track needs
*Relocate*. Prefer fixing filenames before importing into rekordbox.

## rekordbox exports are not ours to synthesize

A stick the CDJ can browse as playlists needs `PIONEER/` with `.DAT`/`.EXT`
analysis, cue points and waveforms, written by rekordbox's *Export to device*.
Copying audio files onto a stick gives you files, not playlists: no cues, no
waveform, and the deck analyses every track at load time.

Nothing in this repo should try to generate that structure. The split is:
this pipeline produces correct, playable files; rekordbox puts them on the
stick.

Health checks that are safe to run on an export stick:

- `diskutil verifyVolume /dev/diskNsM` — needs rekordbox **closed**, it holds
  the volumes and dissents the unmount.
- The count of `PIONEER/**/*.DAT` + `*.EXT` should exceed the audio file count
  (each track has both). If it doesn't, the export is incomplete.
- `._*` files are macOS AppleDouble sidecars, 4 KB each, created on FAT32/exFAT.
  They are not audio and not corruption. On a 6000-track stick expect ~1000 of
  them; tools that scan for audio must skip them or they show up as a mass of
  unreadable files.

## AIFF metadata

ffmpeg writes AIFF tags as ID3v2 and needs `-write_id3v2 1`. `-map_metadata 0`
alone is not reliable for AIFF output — pass each tag explicitly with
`-metadata k=v` when rewriting a library file, and verify afterwards.

`TKEY` (musical key) and `TBPM` matter for harmonic mixing and are silently
dropped along with everything else if this is missed.

When verifying, the ffprobe incantation is `-show_entries format_tags`.
`-show_entries format=format_tags` is invalid and returns **no tags at all**,
which looks exactly like total metadata loss.
