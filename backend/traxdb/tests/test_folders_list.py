"""folders_list ordering — newest blog date first, even under annotate()."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from traxdb.models import ScrapedFolder


class FoldersListOrderingTestCase(TestCase):
    def _make(self, folder_id, inferred_date, scraped_at, status='downloaded'):
        f = ScrapedFolder.objects.create(
            folder_id=folder_id,
            url='https://traxdb2.blogspot.com/p.html',
            pixeldrain_url=f'https://pixeldrain.com/l/{folder_id}',
            inferred_date=inferred_date,
            download_status=status,
        )
        # scraped_at is auto_now_add, so backdate it explicitly.
        ScrapedFolder.objects.filter(pk=f.pk).update(scraped_at=scraped_at)
        return f

    def _ids(self, **params):
        resp = self.client.get('/api/traxdb/folders/', params)
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_freshly_synced_lists_are_not_pushed_off_the_page(self):
        """Regression: `.annotate()` drops Meta.ordering (Django >= 3.1), so a
        freshly synced list landed past the frontend's limit and the panel
        looked like sync had found nothing."""
        old = timezone.now() - timedelta(days=90)
        fresh = timezone.now()
        for i in range(3):
            self._make(f'old{i}', f'2026-01-0{i + 1}', old + timedelta(seconds=i))
        self._make('newlist', '2026-07-15', fresh, status='pending')

        data = self._ids(limit=1)
        self.assertEqual(data['total'], 4)
        self.assertEqual([f['folder_id'] for f in data['results']], ['newlist'])

    def test_batch_is_ordered_by_blog_date_not_scrape_order(self):
        """One sync inserts newest-post-first, so scrape order runs backwards
        against the dates shown in the UI. Blog date wins."""
        t = timezone.now()
        # Insertion order mirrors a real sync: newest post scraped first.
        self._make('jul15', '2026-07-15', t)
        self._make('jul13', '2026-07-13', t + timedelta(seconds=1))
        self._make('jul11', '2026-07-11', t + timedelta(seconds=2))

        data = self._ids(limit=10)
        self.assertEqual(
            [f['folder_id'] for f in data['results']],
            ['jul15', 'jul13', 'jul11'],
        )

    def test_undated_folders_sort_last(self):
        t = timezone.now()
        self._make('nodate', '', t)
        self._make('dated', '2026-07-15', t + timedelta(seconds=1))

        data = self._ids(limit=10)
        self.assertEqual(
            [f['folder_id'] for f in data['results']],
            ['dated', 'nodate'],
        )

    def test_same_date_falls_back_to_scrape_order_then_id(self):
        t = timezone.now()
        a = self._make('dupA', '2026-07-15', t)
        b = self._make('dupB', '2026-07-15', t)

        data = self._ids(limit=10)
        # Identical date and scraped_at — the -id tie-breaker keeps paging stable.
        self.assertEqual(
            [f['folder_id'] for f in data['results']],
            ['dupB', 'dupA'],
        )
        self.assertGreater(b.pk, a.pk)
