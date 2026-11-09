import json
from subprocess import CompletedProcess
from unittest import TestCase
from unittest.mock import patch

from wanted.services.online_source import youtube_source


class OnlineSourceTests(TestCase):
    @patch('wanted.services.online_source.subprocess.run')
    def test_youtube_fallback_keeps_the_requested_mix(self, run):
        run.return_value = CompletedProcess([], 0, stdout=json.dumps({'entries': [
            {'id': 'wrongmix001', 'title': 'Fixture Artist - Tone (Radio Edit)'},
            {'id': 'bad-owner01', 'title': 'Tone (Dub Mix)', 'channel': 'Another Artist - Topic'},
            {'id': 'correctmix1', 'title': 'Tone (Dub Mix)', 'channel': 'Fixture Artist - Topic'},
        ]}))
        self.assertEqual(youtube_source('Fixture Artist', 'Tone (Dub Mix)'),
                         'https://www.youtube.com/watch?v=correctmix1')
        self.assertIn('--flat-playlist', run.call_args.args[0])
        self.assertIn('Fixture Artist Tone (Dub Mix)', run.call_args.args[0][-1])

    @patch('wanted.services.online_source.subprocess.run')
    def test_youtube_fallback_does_not_guess_from_first_result(self, run):
        run.return_value = CompletedProcess([], 0, stdout=json.dumps({'entries': [
            {'id': 'wrongmix001', 'title': 'Fixture Artist - Tone (Club Mix)'},
        ]}))
        self.assertEqual(youtube_source('Fixture Artist', 'Tone (Dub Mix)'), '')
