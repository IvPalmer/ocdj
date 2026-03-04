from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organize', '0001_initial'),
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
                    ('failed', 'Failed'),
                ],
                default='downloaded',
                max_length=20,
            ),
        ),
    ]
