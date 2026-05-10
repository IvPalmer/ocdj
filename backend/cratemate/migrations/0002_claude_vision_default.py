"""Switch the default recognition backend from Gemini to Claude vision.

Both changes are Python-side metadata (default value + choices list +
blank=True). Django records them as `AlterField` operations but no actual
SQL ALTER TABLE runs — safe to apply on a populated DB.

Image_hash gains `blank=True` so manual lookups (no image) can persist as
AlbumIdentification rows without a synthetic hash.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cratemate', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='albumidentification',
            name='image_hash',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AlterField(
            model_name='albumidentification',
            name='method',
            field=models.CharField(
                choices=[
                    ('claude_vision', 'Claude vision (Max OAuth)'),
                    ('gemini', 'Gemini Vision (legacy V1)'),
                    ('vision_ocr', 'Google Vision OCR'),
                    ('universal', 'Universal CLIP search'),
                    ('manual', 'Manual artist+album entry'),
                ],
                default='claude_vision',
                max_length=20,
            ),
        ),
    ]
