# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wanted', '0005_alter_wantedsource_source_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='importoperation',
            name='import_type',
            field=models.CharField(
                choices=[
                    ('youtube', 'YouTube'),
                    ('soundcloud', 'SoundCloud'),
                    ('spotify', 'Spotify'),
                    ('discogs', 'Discogs'),
                    ('bandcamp', 'Bandcamp'),
                ],
                max_length=20,
            ),
        ),
    ]
