"""JPEG/cache proofs that need neither a database nor a running API."""
import io
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from django.test import override_settings
from PIL import Image
from rest_framework.exceptions import ValidationError
from . import thumbnails


def sample_jpeg(size=(640, 480)):
    out = io.BytesIO()
    image = Image.new('RGB', size, '#305070')
    exif = image.getexif(); exif[270] = 'private fixture metadata'
    image.save(out, 'JPEG', exif=exif)
    return out.getvalue()


class ThumbnailCacheTests(TestCase):
    def test_jpeg_is_small_real_and_strips_metadata(self):
        body = thumbnails.jpeg(sample_jpeg())
        with Image.open(io.BytesIO(body)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (320, 240))
            self.assertFalse(image.getexif())
            image.load()
        self.assertLess(len(body), thumbnails.MAX_BYTES)

    def test_spoofed_oversized_and_huge_images_rejected(self):
        for raw in [b'\xff\xd8\xff' + b'x' * 100, b'<script>not an image</script>',
                    b'x' * (thumbnails.MAX_BYTES + 1), sample_jpeg((2100, 2100))]:
            with self.assertRaises(ValidationError):
                thumbnails.jpeg(raw)

    def test_private_cache_cap_and_opaque_names(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(INSTAGRAM_CACHE_ROOT=Path(directory)):
            with thumbnails.locked():
                key = thumbnails.store(sample_jpeg())
                self.assertRegex(key, r'^[0-9a-f]{32}$')
                self.assertEqual(thumbnails.path(key).stat().st_mode & 0o777, 0o600)
                self.assertEqual(thumbnails.root().stat().st_mode & 0o777, 0o700)
                with patch.object(thumbnails, 'MAX_TOTAL_BYTES', 1):
                    with self.assertRaises(ValidationError):
                        thumbnails.store(sample_jpeg())
            with self.assertRaises(ValidationError):
                thumbnails.path('../../escape')
            self.assertEqual(len(list(thumbnails.root().glob('[0-9a-f]' * 32))), 1)
