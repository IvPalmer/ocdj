from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('wanted', '0005_alter_wantedsource_source_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='WantedCleanupIntent',
            fields=[
                ('event_id', models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ('snapshot', models.JSONField(editable=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('wanted', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cleanup_intents', to='wanted.wanteditem',
                )),
            ],
        ),
    ]
