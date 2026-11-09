from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('instagram', '0002_media_source_requests')]
    operations = [
        migrations.AddField(model_name='post', name='thumbnail_key', field=models.CharField(blank=True, max_length=32)),
        migrations.AddField(model_name='post', name='thumbnail_until', field=models.DateTimeField(null=True)),
    ]
