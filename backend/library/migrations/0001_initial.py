from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='LibraryTrack',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_path', models.CharField(max_length=1000, unique=True)),
                ('file_mtime', models.FloatField(default=0, help_text='File modification time for incremental scan')),
                ('artist', models.CharField(blank=True, max_length=500)),
                ('title', models.CharField(blank=True, max_length=500)),
                ('album', models.CharField(blank=True, max_length=500)),
                ('label', models.CharField(blank=True, max_length=500)),
                ('catalog_number', models.CharField(blank=True, max_length=100)),
                ('genre', models.CharField(blank=True, max_length=200)),
                ('year', models.CharField(blank=True, max_length=10)),
                ('format', models.CharField(blank=True, choices=[('mp3', 'MP3'), ('flac', 'FLAC'), ('aiff', 'AIFF'), ('wav', 'WAV'), ('ogg', 'OGG'), ('m4a', 'M4A')], max_length=10)),
                ('bitrate', models.IntegerField(blank=True, help_text='Bitrate in kbps', null=True)),
                ('sample_rate', models.IntegerField(blank=True, help_text='Sample rate in Hz', null=True)),
                ('duration_seconds', models.FloatField(blank=True, null=True)),
                ('file_size_bytes', models.BigIntegerField(default=0)),
                ('has_artwork', models.BooleanField(default=False)),
                ('source', models.CharField(choices=[('organize_pipeline', 'Organize Pipeline'), ('traxdb', 'TraxDB'), ('manual', 'Manual')], default='organize_pipeline', max_length=20)),
                ('missing', models.BooleanField(default=False, help_text='File no longer exists on disk')),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-added_at'],
            },
        ),
    ]
