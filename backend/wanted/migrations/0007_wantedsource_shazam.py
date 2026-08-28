from django.db import migrations, models


class Migration(migrations.Migration):
    """Add 'shazam' to WantedSource.source_type.

    `WantedItem.identified_via` already had 'shazam' — what was missing was a
    *source* for it, i.e. somewhere Shazams arrive from rather than a way they
    were identified.
    """

    dependencies = [
        ('wanted', '0006_add_bandcamp_import_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='wantedsource',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('manual', 'Manual'),
                    ('blog', 'Blog'),
                    ('spotify', 'Spotify'),
                    ('soundcloud', 'SoundCloud'),
                    ('youtube', 'YouTube'),
                    ('telegram', 'Telegram'),
                    ('discogs', 'Discogs'),
                    ('bandcamp', 'Bandcamp'),
                    ('dig', 'Dig'),
                    ('shazam', 'Shazam'),
                ],
                default='manual',
                max_length=50,
            ),
        ),
    ]
