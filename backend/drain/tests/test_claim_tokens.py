"""Claim tokens: a confirmation may only ever archive the bytes it claimed.

The lock in `claim_publishable` ends the moment the response is written, and
the Mac then spends minutes downloading. The sequence that destroys the only
copy of a track is: Mac claims artifact A → operator edits the track, so the
file becomes B and the row goes back to publishable → Mac confirms A → server
deletes B and marks the row archived, with nothing on the Mac but A.

So each claim mints a token, confirm/fail must echo it, and anything that
replaces the published bytes clears it. A lease check can't cover this: the
daemon is allowed to confirm late, and "late" is exactly when the artifact has
had time to change.
"""

import os
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from organize.models import PipelineItem
from organize.services.publisher import claim_publishable, compute_sha256
from organize.services.refresh import refresh_published_artifact
from organize.tests.test_published_refresh import PublishedArtifactTestCase

TOKEN = 'test-drain-token'
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {TOKEN}'}


class DrainClaimTestCase(PublishedArtifactTestCase):
    def setUp(self):
        super().setUp()
        self.token_env = patch.dict(os.environ, {'DRAIN_TOKEN': TOKEN})
        self.token_env.start()

    def tearDown(self):
        self.token_env.stop()
        super().tearDown()

    def claim(self):
        resp = self.client.get('/api/drain/publishable/', **AUTH)
        self.assertEqual(resp.status_code, 200)
        return resp.json()['items']

    def confirm(self, item_id, claim_token=None, persistent_id='ABC123'):
        body = {'music_persistent_id': persistent_id}
        if claim_token is not None:
            body['claim_token'] = claim_token
        return self.client.post(
            f'/api/drain/{item_id}/confirm/',
            data=body,
            content_type='application/json',
            **AUTH,
        )

    def fail(self, item_id, claim_token=None, reason='rsync failed'):
        body = {'reason': reason}
        if claim_token is not None:
            body['claim_token'] = claim_token
        return self.client.post(
            f'/api/drain/{item_id}/fail/',
            data=body,
            content_type='application/json',
            **AUTH,
        )


class ClaimTokenIssueTests(DrainClaimTestCase):
    def test_claim_hands_out_a_token_and_stores_it(self):
        item = self.make_published()

        claimed = self.claim()

        self.assertEqual(len(claimed), 1)
        self.assertTrue(claimed[0]['claim_token'])
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'draining')
        self.assertEqual(item.claim_token, claimed[0]['claim_token'])

    def test_each_claim_gets_its_own_token(self):
        self.make_published(filename='one.wav')
        self.make_published(filename='two.wav')

        claimed = self.claim()

        tokens = {row['claim_token'] for row in claimed}
        self.assertEqual(len(tokens), 2)


class ConfirmRequiresLiveClaimTests(DrainClaimTestCase):
    def test_confirm_without_a_token_is_refused(self):
        item = self.make_published()
        self.claim()

        resp = self.confirm(item.id)

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'draining')
        self.assertTrue(os.path.exists(item.work_path))

    def test_confirm_with_a_wrong_token_is_refused(self):
        item = self.make_published()
        self.claim()

        resp = self.confirm(item.id, claim_token='f' * 32)

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'draining')
        self.assertTrue(os.path.exists(item.work_path))

    def test_confirm_on_an_unclaimed_publishable_row_is_refused(self):
        # The old code accepted archive_state=publishable with no claim at all.
        item = self.make_published()

        resp = self.confirm(item.id, claim_token='f' * 32)

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'publishable')
        self.assertTrue(os.path.exists(item.work_path))

    def test_confirm_with_the_live_token_archives_and_deletes(self):
        item = self.make_published()
        claimed = self.claim()
        publish_dir = os.path.dirname(item.work_path)

        resp = self.confirm(item.id, claim_token=claimed[0]['claim_token'])

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'archived')
        self.assertEqual(item.work_path, '')
        self.assertEqual(item.claim_token, '')
        self.assertFalse(os.path.exists(publish_dir))


class StaleClaimTests(DrainClaimTestCase):
    def test_an_edit_after_a_released_claim_invalidates_the_confirmation(self):
        """The data-loss sequence, replayed end to end.

        The Mac claims artifact A and starts downloading. Its cycle reports a
        failure (or times out), which releases the claim and puts the row back
        in the pool. The operator then edits the track, so the published bytes
        become B. A late confirmation for A must not delete B.
        """
        item = self.make_published()
        claimed = self.claim()
        stale_token = claimed[0]['claim_token']
        self.assertEqual(self.fail(item.id, claim_token=stale_token).status_code, 200)

        refresh_published_artifact(item.id, metadata={'title': 'Attic'})
        item.refresh_from_db()
        new_bytes = compute_sha256(item.work_path)

        resp = self.confirm(item.id, claim_token=stale_token)

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'publishable')
        self.assertTrue(os.path.exists(item.work_path))
        self.assertEqual(compute_sha256(item.work_path), new_bytes)

    def test_an_edit_is_refused_outright_while_the_claim_is_live(self):
        item = self.make_published()
        self.claim()

        resp = self.client.post(
            f'/api/organize/pipeline/{item.id}/refresh/',
            data={'title': 'Attic'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.title, 'Basement')
        self.assertEqual(item.archive_state, 'draining')

    def test_a_reclaim_after_lease_expiry_invalidates_the_old_token(self):
        item = self.make_published()
        first = self.claim()[0]['claim_token']
        PipelineItem.objects.filter(pk=item.pk).update(
            draining_until=timezone.now() - timedelta(minutes=1),
        )

        second = self.claim()[0]['claim_token']
        self.assertNotEqual(first, second)

        self.assertEqual(self.confirm(item.id, claim_token=first).status_code, 409)
        self.assertEqual(self.confirm(item.id, claim_token=second).status_code, 200)

    def test_fail_also_requires_the_live_token(self):
        item = self.make_published()
        self.claim()

        resp = self.fail(item.id, claim_token='f' * 32)

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.drain_attempts, 0)

    def test_fail_with_the_live_token_releases_the_claim(self):
        item = self.make_published()
        claimed = self.claim()

        resp = self.fail(item.id, claim_token=claimed[0]['claim_token'])

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'publishable')
        self.assertEqual(item.drain_attempts, 1)
        self.assertEqual(item.claim_token, '')


class PublishDirValidationTests(DrainClaimTestCase):
    """Confirm deletes a directory tree; it may only ever be <publish>/<id>/."""

    def test_confirm_refuses_a_work_path_outside_the_publish_dir(self):
        item = self.make_published()
        claimed = self.claim()
        intruder_dir = os.path.join(self.tmpdir.name, 'not-ours')
        os.makedirs(intruder_dir, exist_ok=True)
        with open(os.path.join(intruder_dir, 'keepme.txt'), 'w') as fh:
            fh.write('not the drain daemon\'s business')
        PipelineItem.objects.filter(pk=item.pk).update(
            work_path=os.path.join(intruder_dir, 'track.wav'),
        )

        resp = self.confirm(item.id, claim_token=claimed[0]['claim_token'])

        self.assertEqual(resp.status_code, 409)
        self.assertTrue(os.path.isdir(intruder_dir))
        self.assertTrue(os.path.exists(os.path.join(intruder_dir, 'keepme.txt')))
        item.refresh_from_db()
        self.assertNotEqual(item.archive_state, 'archived')

    def test_claim_marks_a_noncanonical_row_failed_instead_of_wedging_it(self):
        # Confirm refuses to delete outside <publish>/<id>/, so handing such a
        # row to the daemon would loop forever: claimed, undeletable, and
        # unrepairable because 'draining' rejects edits.
        item = self.make_published()
        stray = os.path.join(self.tmpdir.name, 'not-ours', 'track.wav')
        os.makedirs(os.path.dirname(stray), exist_ok=True)
        with open(stray, 'wb') as fh:
            fh.write(b'RIFF')
        PipelineItem.objects.filter(pk=item.pk).update(work_path=stray)

        claimed = self.claim()

        self.assertEqual(claimed, [])
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'failed')
        self.assertEqual(item.claim_token, '')
        self.assertTrue(os.path.exists(stray))

    def test_claim_marks_a_row_failed_when_the_work_path_is_gone(self):
        item = self.make_published()
        os.remove(item.work_path)

        claimed = self.claim()

        self.assertEqual(claimed, [])
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'failed')
        self.assertEqual(item.claim_token, '')
