# Organize Pipeline

The organize pipeline is a disk-backed workbench for one audio file at a time.
`PipelineItem.stage` is the database state, and the numbered folders under
`SOULSEEK_DOWNLOAD_ROOT` are the matching operator-facing checkpoints.

## Stage Folders

| DB stage | Folder | Owner | What happens |
| --- | --- | --- | --- |
| `downloaded` | `01_downloaded` | `organize.services.pipeline` | New completed downloads, uploads, and scanned orphan audio files enter here. |
| `tagged` | `02_tagged` | `organize.services.tagger` | Existing tags, Wanted metadata, filename metadata, optional enrichment, and artwork are written to the file. |
| `renamed` | `03_renamed` | `organize.services.renamer` | The file is renamed from the configured template, using cleaned artist/title values. |
| `converted` | `04_converted` | `organize.services.converter` | Conversion rules decide whether to keep, skip, or convert format with ffmpeg. |
| `ready` | `05_ready` | `organize.services.pipeline` | The file is ready for manual review or publishing. Linked Wanted items become `organized`. |
| `published` | `06_publish` | `organize.services.publisher` | VPS mode copies bytes into a drainable package for the home Mac import daemon. |

Transient DB stages (`tagging`, `renaming`, `converting`) do not have folders.
They mark the service currently mutating the file before the pipeline moves it
to the next durable folder.

## Entry Points

- Soulseek completion: `soulseek.views.downloads` detects a completed slskd
  transfer and schedules `organize.services.pipeline.auto_ingest_download`.
- Cron/polling completion: `soulseek.management.commands.check_downloads`
  marks `Download` and linked Wanted/queue state as downloaded. The organize
  scan endpoint can ingest those completed rows later.
- Manual scan: `POST /api/organize/pipeline/scan/` calls
  `scan_completed_downloads`, which ingests completed `Download` rows and
  untracked audio files already sitting in `01_downloaded`.
- Upload: `POST /api/organize/pipeline/upload/` writes multipart audio files
  directly into `01_downloaded` and creates `PipelineItem` rows.
- Kick: `POST /api/organize/pipeline/kick/` scans, then starts processing
  pending `downloaded` items.

## Processing Flow

`process_pipeline_item` owns the overall sequence:

1. `downloaded` -> `tagging` -> `tagged`
2. optional agent enrichment while still `tagged`
3. `tagged` -> `renaming` -> `renamed`
4. `renamed` -> `converting` -> `converted`
5. `converted` -> `ready`
6. optional auto-publish when `OCDJ_AUTOPUBLISH=1`

Each service mutates the file in its current folder. After the service returns,
the pipeline service moves the resulting file to the next numbered folder and
updates `PipelineItem.current_path` and `PipelineItem.stage` together. Name
collisions are resolved by appending `_1`, `_2`, and so on.

## State Sync

When a Soulseek `Download` is ingested, the pipeline item links back to that
download and copies Wanted metadata into editable organize fields. After the
file is moved into `01_downloaded`, `Download.local_path` is updated to the new
pipeline path so later API responses and repair jobs point at the tracked file.

Wanted status changes are intentionally narrow:

- after tagging succeeds: `tagged`
- after the item reaches `ready`: `organized`

Failures set `PipelineItem.stage='failed'` and store a stage-specific error
message. Retry currently resets the item to `downloaded` and runs the sequence
again.
