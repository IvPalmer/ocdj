# How OCDJ works, and how to set it up for one person

Read this after [INSTALL.md](INSTALL.md) has the stack running. It explains the
flow, what each tab needs, and what to ask the person so the app fits their
workflow.

If you are a coding agent: do the interview in section 3 before changing
settings, set only what the answers call for, and leave the rest off. Never
invent a credential; ask the person to create it and paste it into `.env` or
the Settings tab themselves.

## 1. The flow

```
sources ──> Wanted ──> Soulseek search ──> download ──> Organize ──> 05_ready ──> your library
(playlists,   (the list     (slskd)                      (tag, rename,
 Shazam,       of tracks                                  convert)
 recognize,    you want)
 manual)
```

1. **Wanted** is the list of tracks the person wants. Tracks get there by hand,
   by importing a playlist, from recognition results, or from the browser
   extension.
2. **Soulseek** searches the Soulseek network for a Wanted track and downloads
   the file the person picks.
3. **Organize** takes each finished download through numbered folders: tag,
   rename, convert. The file ends in `05_ready`.
4. The person listens, then moves the file from `05_ready` into their own
   library folder. In a local install nothing moves it for them.

## 2. Tabs and what each one needs

| Tab | What it does | Needs |
| --- | --- | --- |
| Dashboard | Counts and recent activity | nothing |
| Wanted | The want list; import from Spotify, SoundCloud, YouTube, Discogs, Bandcamp | Per source: Spotify client ID and secret; SoundCloud client ID and secret; YouTube API key; Discogs personal token and username |
| Soulseek | Search and download | A Soulseek account in `.env` and the stack started with `--profile soulseek` |
| Organize | Tag, rename, convert downloads | Nothing. AcoustID key improves tagging |
| Library | Browse what is in the music folder | nothing |
| Recognize | Identify the tracks in a DJ set from a YouTube or SoundCloud link | Works with Shazam out of the box; ACRCloud keys and a trackid.net token add coverage |
| Shazam | Pull the person's Shazam history into Wanted | Goes through Spotify: Shazam must be set to add its tags to a Spotify playlist, and Spotify must be connected |
| YouTube | Download audio from a YouTube link | Nothing; YouTube cookies help when YouTube asks for sign-in |
| CrateMate | Identify a record from a photo of its cover | A `CLAUDE_CODE_OAUTH_TOKEN` in `.env` (from `claude setup-token`) and a Discogs token |
| Agent | Chat with an agent that can drive the app | The host sidecar in `ocdj-sidecar/` |
| TraxDB, Instagram | Built around the maintainer's own accounts and Mac | Treat as unsupported in a local install |
| Settings | Every key, path and threshold | nothing |

Only Soulseek and Organize are needed for the core flow. Everything else is
optional and can be added later.

## 3. Interview: what to ask the person

1. Where is your music library today, and where should finished tracks go?
   (Sets `OCDJ_MUSIC_DIR`. Point it at a folder OCDJ may write to; it can be a
   new folder next to the existing library rather than the library itself.)
2. Do you have a Soulseek account for this, and a folder of music to share?
   (Sets `SLSKD_SLSK_USERNAME`, `SLSKD_SLSK_PASSWORD`, `SLSKD_SHARE_DIR`.)
3. Where do you keep track of music you want: Spotify playlists, SoundCloud
   likes, YouTube playlists, a Discogs wantlist, Shazam? (Decides which source
   keys are worth creating.)
4. Which file format does your DJ software want? (Sets
   `ORGANIZE_CONVERSION_RULES`. The default converts WAV and FLAC to AIFF and
   keeps MP3 at 320 kbps.)
5. How should files be named? (Sets `ORGANIZE_RENAME_TEMPLATE`. Default:
   `{artist} - {title}`.)
6. Do you want anything to run without you clicking, or do you approve each
   download? (Sets the automation switches. Default: all off.)

## 4. Where settings live

Two places:

- **`.env`** in the repo folder: the music folder, the Soulseek account, and
  optional first values for API keys. Changing it needs
  `docker compose --profile soulseek up -d` to take effect.
- **The Settings tab** (http://localhost:5174/settings): every other key, path
  and threshold, stored in the database. A value saved there wins over `.env`.

An agent can read and write the same store over HTTP:

- `GET http://localhost:8002/api/core/config/schema/` lists every setting with
  its group, type, default and description.
- `GET http://localhost:8002/api/core/config/` shows current values.
- `POST http://localhost:8002/api/core/config/update/` with a JSON body of
  `{"KEY": "value"}` pairs saves them. Unknown keys are ignored.

### Settings most people touch

| Setting | Default | Meaning |
| --- | --- | --- |
| `ORGANIZE_RENAME_TEMPLATE` | `{artist} - {title}` | Filename. Placeholders: `{artist}`, `{title}`, `{label}`, `{catalog}`, `{year}` |
| `ORGANIZE_CONVERSION_RULES` | `wav -> aiff`, `flac -> aiff`, `mp3>=320k -> keep` | One rule per line: `format[>=bitrate] -> target` or `keep` |
| `REVIEW_FOLDER` | `/music/Electronic/_Review` | Review staging folder for cleaned tracks before they go into the library |
| `SPOTIFY_DEFAULT_PLAYLIST`, `YOUTUBE_DEFAULT_PLAYLIST`, `SC_DEFAULT_PLAYLIST` | empty | The playlist each import reads by default |
| `AUTOMATION_ENABLED` | off | Master switch for the automatic Wanted → search → download → organize chain |
| `AUTOMATION_AUTO_SEARCH`, `AUTOMATION_AUTO_DOWNLOAD`, `AUTOMATION_AUTO_ORGANIZE` | off | One switch per step |
| `AUTOMATION_CONFIDENCE_THRESHOLD` | 85 | Minimum match score for an automatic download |

Paths in Settings are paths inside the containers: they start with `/music`,
which is `OCDJ_MUSIC_DIR` on the host.

Leave `TRAXDB_*`, `BLOGGER_*` and `PIXELDRAIN_API_KEY` empty.

## 5. Folders on disk

Inside `OCDJ_MUSIC_DIR/Electronic/ID3/soulseek/`:

| Folder | Stage |
| --- | --- |
| `01_downloaded` | A finished download, or a file dropped in by hand |
| `02_tagged` | Tags and artwork written |
| `03_renamed` | Renamed from the template |
| `04_converted` | Converted by the rules |
| `05_ready` | Done. Listen, then move it into the library |
| `collection` | Shared on Soulseek, read-only, unless `SLSKD_SHARE_DIR` is set |

A file dropped into `01_downloaded` by hand is picked up by the "Scan inbox" button in
Organize, so the pipeline also cleans music that did not come from Soulseek.

`06_publish` belongs to the maintainer's VPS setup and stays unused locally.

## 6. First run, to prove it works

1. Add one track by hand in Wanted.
2. Search it in Soulseek and download one result.
3. Open Organize and watch the file move to `05_ready`.
4. Check the file in `OCDJ_MUSIC_DIR/Electronic/ID3/soulseek/05_ready`.

If step 2 finds nothing, check `docker compose logs slskd` for a login error
before changing anything else.

## 7. What a local install does not do

- It does not put files into Music.app, Rekordbox or any other library. The
  hand-off is the `05_ready` folder.
- It has no login screen. Keep it on `localhost`; do not expose the ports to
  the internet.
- The maintainer's daemons, VPS and Safari extension are not part of it.
