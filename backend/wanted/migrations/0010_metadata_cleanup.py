from django.db import migrations, models
import django.utils.timezone


def protect_legacy(apps, schema_editor):
    # Unknown legacy provenance must never acquire automatic-write authority.
    apps.get_model('wanted', 'WantedItem').objects.using(schema_editor.connection.alias).update(metadata_protected=True)


class Migration(migrations.Migration):
    dependencies = [('wanted', '0009_wantedsource_cursor')]
    operations = [
        migrations.AddField('wanteditem', 'cleanup_isrc', models.CharField(max_length=12, blank=True, editable=False)),
        migrations.AddField('wanteditem', 'metadata_revision', models.PositiveBigIntegerField(default=0, editable=False)),
        migrations.AddField('wanteditem', 'metadata_protected', models.BooleanField(default=False, editable=False)),
        migrations.RunPython(protect_legacy, migrations.RunPython.noop),
        migrations.CreateModel(name='MetadataCleanup', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('subject_key', models.CharField(max_length=200, unique=True)),
            ('wanted_id', models.PositiveBigIntegerField(null=True, blank=True)),
            ('job_id', models.PositiveBigIntegerField(null=True, blank=True)),
            ('snapshot', models.JSONField()),
            ('evidence', models.JSONField(default=dict)),
            ('result', models.JSONField(default=dict)),
            ('status', models.CharField(max_length=20, default='queued')),
            ('attempts', models.PositiveSmallIntegerField(default=0)),
            ('lease', models.UUIDField(null=True)),
            ('available_at', models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
            ('created', models.DateTimeField(auto_now_add=True)),
            ('updated', models.DateTimeField(auto_now=True)),
        ]),
    ]
