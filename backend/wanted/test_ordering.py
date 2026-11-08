from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase
from wanted.models import WantedItem


class WantedOrderingTests(APITestCase):
    def test_new_items_are_on_first_page_with_stable_ties_and_user_sort(self):
        older = [WantedItem.objects.create(artist='Old', title=f'Old {i}') for i in range(51)]
        newest = [WantedItem.objects.create(artist='New', title=f'New {i}') for i in range(2)]
        stamp = timezone.now()
        WantedItem.objects.filter(pk__in=[x.pk for x in older]).update(added=stamp - timedelta(days=1))
        WantedItem.objects.filter(pk__in=[x.pk for x in newest]).update(added=stamp)
        response = self.client.get('/api/wanted/items/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 53)
        self.assertEqual([x['id'] for x in response.data['results'][:2]], [newest[1].pk, newest[0].pk])
        self.assertEqual(len(response.data['results']), 50)
        page2 = self.client.get('/api/wanted/items/?page=2').data['results']
        self.assertEqual(len({x['id'] for x in response.data['results'] + page2}), 53)
        ascending = self.client.get('/api/wanted/items/?ordering=added').data['results']
        self.assertTrue(all(x['artist'] == 'Old' for x in ascending))
        searched = self.client.get('/api/wanted/items/?search=New').data
        self.assertEqual(searched['count'], 2)
        self.assertEqual(searched['results'][0]['id'], newest[1].pk)
