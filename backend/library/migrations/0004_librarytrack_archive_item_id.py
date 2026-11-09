from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('library', '0003_macplayback'),
    ]

    operations = [
        migrations.AddField(
            model_name='librarytrack',
            name='archive_item_id',
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
