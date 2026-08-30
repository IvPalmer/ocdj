from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('wanted', '0008_wanteditem_preview'),
    ]

    operations = [
        migrations.AddField(
            model_name='wantedsource',
            name='cursor',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
