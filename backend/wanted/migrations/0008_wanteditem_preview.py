from django.db import migrations, models


class Migration(migrations.Migration):
    """Cache a 30s preview per track.

    `preview_checked` is stored even for a miss: much of what this app tracks
    is promo material no catalogue carries, and without recording the miss the
    list would re-ask iTunes and Deezer on every render.
    """

    dependencies = [
        ('wanted', '0007_wantedsource_shazam'),
    ]

    operations = [
        migrations.AddField(
            model_name='wanteditem',
            name='preview_url',
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name='wanteditem',
            name='preview_provider',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='wanteditem',
            name='preview_checked',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
