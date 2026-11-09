from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('instagram', '0001_initial')]
    operations = [
        migrations.AddField(model_name='media', name='lease_action', field=models.CharField(max_length=16, blank=True)),
        migrations.AddField(model_name='media', name='recognition', field=models.JSONField(default=dict)),
        migrations.AddField(model_name='media', name='playback_requested', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='media', name='playback_error', field=models.BooleanField(default=False)),
    ]
