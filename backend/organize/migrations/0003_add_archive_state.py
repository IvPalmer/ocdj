from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organize', '0002_add_conversion_stages'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pipelineitem',
            name='stage',
            field=models.CharField(
                choices=[
                    ('downloaded', 'Downloaded'),
                    ('tagging', 'Tagging'),
                    ('tagged', 'Tagged'),
                    ('renaming', 'Renaming'),
                    ('renamed', 'Renamed'),
                    ('converting', 'Converting'),
                    ('converted', 'Converted'),
                    ('ready', 'Ready'),
                    ('published', 'Published'),
                    ('failed', 'Failed'),
                ],
                default='downloaded',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='archive_state',
            field=models.CharField(
                choices=[
                    ('on_workbench', 'On workbench'),
                    ('publishable', 'Publishable'),
                    ('draining', 'Draining'),
                    ('archived', 'Archived'),
                    ('failed', 'Failed'),
                ],
                default='on_workbench',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='sha256',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='work_path',
            field=models.CharField(blank=True, default='', max_length=1000),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='music_persistent_id',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='drain_attempts',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pipelineitem',
            name='draining_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='pipelineitem',
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(archive_state__in=['publishable', 'draining'])
                    | ~models.Q(work_path='')
                ),
                name='workpath_set_when_publishable_or_draining',
            ),
        ),
        migrations.AddConstraint(
            model_name='pipelineitem',
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(archive_state='archived')
                    | (
                        models.Q(work_path='')
                        & models.Q(archived_at__isnull=False)
                        & ~models.Q(music_persistent_id='')
                    )
                ),
                name='archived_means_vps_bytes_gone',
            ),
        ),
        migrations.AddConstraint(
            model_name='pipelineitem',
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(archive_state__in=['publishable', 'draining', 'archived'])
                    | ~models.Q(sha256='')
                ),
                name='sha256_set_once_publishable',
            ),
        ),
        migrations.AddIndex(
            model_name='pipelineitem',
            index=models.Index(fields=['archive_state'], name='idx_pipeline_archstate'),
        ),
        migrations.AddIndex(
            model_name='pipelineitem',
            index=models.Index(fields=['archive_state', 'draining_until'], name='idx_pipeline_lease'),
        ),
    ]
