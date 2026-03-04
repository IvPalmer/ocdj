# Generated manually for Phase F: TraxDB Native Rewrite

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('traxdb', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScrapedFolder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('folder_id', models.CharField(help_text='Pixeldrain list ID', max_length=100, unique=True)),
                ('title', models.CharField(blank=True, max_length=500)),
                ('url', models.URLField(blank=True, help_text='Blog post URL where this list was found', max_length=2000)),
                ('pixeldrain_url', models.URLField(blank=True, max_length=2000)),
                ('inferred_date', models.CharField(blank=True, help_text='YYYY-MM-DD inferred from blog post', max_length=10)),
                ('pixeldrain_links', models.JSONField(blank=True, default=list, help_text='List of Pixeldrain URLs found in the post')),
                ('scraped_at', models.DateTimeField(auto_now_add=True)),
                ('download_status', models.CharField(choices=[('pending', 'Pending'), ('downloading', 'Downloading'), ('downloaded', 'Downloaded'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('sync_operation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='scraped_folders', to='traxdb.traxdboperation')),
            ],
            options={
                'ordering': ['-scraped_at'],
            },
        ),
        migrations.CreateModel(
            name='ScrapedTrack',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filename', models.CharField(max_length=500)),
                ('pixeldrain_file_id', models.CharField(blank=True, max_length=100)),
                ('pixeldrain_url', models.URLField(blank=True, max_length=2000)),
                ('local_path', models.CharField(blank=True, max_length=1000)),
                ('file_size_bytes', models.BigIntegerField(blank=True, null=True)),
                ('downloaded', models.BooleanField(default=False)),
                ('download_status', models.CharField(choices=[('pending', 'Pending'), ('downloading', 'Downloading'), ('downloaded', 'Downloaded'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('folder', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tracks', to='traxdb.scrapedfolder')),
            ],
            options={
                'ordering': ['filename'],
            },
        ),
    ]
