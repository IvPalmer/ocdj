from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('organize', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='FetchJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=2000)),
                ('video_id', models.CharField(blank=True, max_length=32)),
                ('title', models.CharField(blank=True, max_length=500)),
                ('uploader', models.CharField(blank=True, max_length=500)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('fetching', 'Fetching'), ('downloaded', 'Downloaded'), ('failed', 'Failed')], default='queued', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('downloaded_path', models.CharField(blank=True, max_length=1000)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('pipeline_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fetch_jobs', to='organize.pipelineitem')),
            ],
            options={
                'ordering': ['-id'],
            },
        ),
    ]
