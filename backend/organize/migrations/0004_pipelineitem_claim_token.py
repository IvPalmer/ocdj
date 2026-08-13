from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organize', '0003_add_archive_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipelineitem',
            name='claim_token',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
