from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('traxdb', '0005_macinventory_file_count_macinventory_total_bytes'),
    ]

    operations = [
        # Why a download failed, so the panel can say more than "1 list failed".
        migrations.AddField(
            model_name='scrapedfolder',
            name='last_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='scrapedfolder',
            name='last_error_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # The Mac daemon's own cadence, reported by the daemon — the server
        # cannot know an interval that lives in a launchd plist.
        migrations.AddField(
            model_name='macinventory',
            name='poll_interval_seconds',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='macinventory',
            name='batch_limit',
            field=models.IntegerField(default=0),
        ),
    ]
