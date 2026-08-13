# Drain lifecycle hardening — receipts

Branch: `task/drain-lifecycle-hardening` (from `origin/vps-deploy`). Not merged,
not deployed, migration not applied.

## Verification

Tests run against branch code inside the prod backend image with the branch
bind-mounted over the baked `/app` (`docker compose exec` would have run the
deployed code and false-passed):

```
ssh main-instance 'cd /home/ubuntu/ocdj && git fetch origin -q \
  && git worktree add --detach /tmp/ocdj-wt origin/task/drain-lifecycle-hardening -q \
  && docker compose run --rm --no-deps -v /tmp/ocdj-wt/backend:/app backend \
     python manage.py test --noinput'
```

| Run | Tests | Result | File |
|---|---|---|---|
| baseline `origin/vps-deploy` | 143 | OK | `backend-tests-before.txt` |
| branch | 183 | OK | `backend-tests-after.txt` |
| frontend `npm run build` (Mac) | — | built in 685ms | `frontend-build.txt` |

`migration-check.txt` — `makemigrations --check --dry-run` on both baseline and
branch. Both report the same two index removals (`idx_pipeline_archstate`,
`idx_pipeline_lease`): pre-existing drift between migration `0003` and
`models.py`, present before this branch and deliberately left alone. The new
field is fully covered by `0004_pipelineitem_claim_token`.

## Which test proves which spec item

1. **Claim tokens** — `drain/tests/test_claim_tokens.py`:
   `ClaimTokenIssueTests.*` (token minted per claim, one per row),
   `ConfirmRequiresLiveClaimTests.test_confirm_without_a_token_is_refused`,
   `…_with_a_wrong_token_is_refused`,
   `…_on_an_unclaimed_publishable_row_is_refused` (the "confirm accepts
   publishable with no claim" hole),
   `…_with_the_live_token_archives_and_deletes` (happy path still works),
   `StaleClaimTests.test_an_edit_after_a_released_claim_invalidates_the_confirmation`
   (full loss sequence: claim → fail → edit → late confirm refused, new bytes
   survive), `…test_a_reclaim_after_lease_expiry_invalidates_the_old_token`
   (lease expiry alone is not the gate — the rotated token is),
   `…test_fail_also_requires_the_live_token`.
   Code: `organize/services/publisher.py::claim_publishable`,
   `drain/views.py::_check_claim_token`.

2. **One published-metadata-refresh operation** —
   `organize/tests/test_published_refresh.py::RefreshServiceTests`:
   `test_refresh_moves_work_path_and_recomputes_hash` (work_path, filename,
   sha256 and embedded tag all move together),
   `test_refresh_clears_the_failure_bookkeeping`,
   `test_refresh_relocates_a_file_left_outside_the_publish_dir`,
   `test_refresh_endpoint_returns_the_updated_row` (also asserts `claim_token`
   is never serialised), `test_refresh_endpoint_409s_on_a_draining_row`.
   Code: `organize/services/refresh.py`, `POST /pipeline/<id>/refresh/`.
   Frontend: `OrganizePanel.jsx::EditModal` sends one call for published rows.

3. **Temp copy + atomic replace** —
   `AtomicTagWriteTests.test_failure_leaves_the_original_bytes_intact` (hash
   unchanged after a mid-write exception, no `.ocdj-tag-*` leftovers),
   `…test_success_replaces_the_file_in_one_step`.
   Code: `organize/services/tagger.py::write_tags_atomic`.

4. **State guards** — `StateGuardTests`:
   `test_patch_refused_while_draining`,
   `test_patch_refused_on_a_published_row_and_points_at_refresh`,
   `test_patch_still_works_on_the_workbench` (no regression),
   `test_delete_refused_while_draining_and_file_survives`,
   `test_retag_refused_on_a_published_row`, plus
   `StaleClaimTests.test_an_edit_is_refused_outright_while_the_claim_is_live`.

5. **`rename_file` invariant** — `RenameInvariantTests`:
   `test_rename_moves_work_path_when_it_tracks_the_same_file`,
   `test_rename_leaves_an_unrelated_work_path_alone`.

6. **Other published-artifact mutators** — `BulkMutatorGuardTests`:
   `test_retag_clean_skips_published_rows` (bytes unchanged, reported as
   `skipped_published`), `test_rerename_all_skips_published_rows`.

7. **Canonical publish-dir validation** —
   `PublishDirValidationTests.test_confirm_refuses_a_work_path_outside_the_publish_dir`
   (unrelated directory and its file still there, row not archived).
   Code: `organize/services/publisher.py::is_canonical_publish_dir`.

8. **Download works for broken items** — `DownloadFallbackTests`:
   `test_stale_work_path_falls_back_to_current_path`,
   `test_failed_items_can_still_be_downloaded` (signed URL streams 200).
   Frontend: `DOWNLOADABLE_STATES` now includes `failed`.

9. **Retry-drain validates** — `RetryDrainTests`:
   `test_retry_refuses_when_the_file_is_gone`,
   `test_retry_refuses_when_the_bytes_no_longer_match`,
   `test_retry_requeues_a_sound_artifact`,
   `test_retry_repairs_a_stale_work_path`.
   Code: `POST /pipeline/<id>/retry-drain/`; frontend `RetryDrainButton`.

10. **Clearing a field clears the tag** — `TagClearingTests`:
    `test_empty_value_clears_the_tag`,
    `test_absent_key_leaves_the_tag_alone` (the pipeline tagger's additive
    behaviour is preserved), `test_refresh_clears_a_field_the_operator_emptied`.

## Deploy prerequisite — the Mac daemon must change first-or-together

`~/bin/ocdj-drain.sh` (not in this repo) does not send `claim_token`. Once this
backend is deployed, every confirm/fail from the current daemon returns 409 and
nothing archives. The daemon needs, in `main()`'s per-track parsing, the
`claim_token` field from `/publishable/`, threaded into `drain_one` and both
API calls:

```sh
api_confirm() {   # id, persistent_id, claim_token
  --data "$(printf '{"music_persistent_id":"%s","claim_token":"%s"}' "$2" "$3")"
}
api_fail() {      # id, reason, claim_token
  --data "$(printf '{"reason":"%s","claim_token":"%s"}' "$2" "$3")"
}
```

Sending the extra field to the *current* backend is harmless (unknown body keys
are ignored), so the daemon can safely be updated first.
