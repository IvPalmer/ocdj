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

## Published Artifacts

Once `publish_pipeline_item` runs, the file lives at `<publish>/<id>/` and the
row carries a `sha256` the Mac drain daemon verifies. From that point the
workbench edit path is closed:

- `PATCH /api/organize/pipeline/<id>/` and `POST .../retag/` are workbench-only
  (`archive_state='on_workbench'`). `retag-clean/` and `rerename/` skip
  published rows and report them as `skipped_published`.
- `POST /api/organize/pipeline/<id>/refresh/` is the only way to change a
  published track. Under a row lock it writes the tags to a temp copy and swaps
  it in, renames, recomputes `sha256`, moves `work_path` with the file, clears
  the failure bookkeeping and puts the row back to `publishable`.
- `POST /api/organize/pipeline/<id>/retry-drain/` re-queues a failed drain only
  after verifying the file exists and still hashes to the recorded `sha256`.
- `draining` and `archived` rows reject metadata edits outright, and `draining`
  also rejects DELETE — the Mac owns those bytes.

### Drain claim tokens

`GET /api/drain/publishable/` returns a per-row `claim_token` alongside
`work_path`/`sha256`. `POST /api/drain/<id>/confirm/` and `.../fail/` must echo
it as `claim_token` in the JSON body; a token that has been rotated (re-claim)
or cleared (refresh, confirm, fail) is refused with 409. The lease
(`draining_until`) only decides who may claim next — it is not what authorises
a confirmation, because a late confirmation is exactly the case where the
artifact may already have been replaced.

Confirm also refuses to `rmtree` anything that is not exactly
`<publish>/<id>/`.

**Daemon contract:** the Mac daemon (`elder-brain` repo,
`scripts/ocdj-drain/ocdj-drain.sh`, installed as `~/bin/ocdj-drain.sh`) must
send the token back on confirm/fail. Deploying this backend without that change
makes every drain confirmation 409. The daemon can be updated first: the
current backend ignores unknown body keys.
