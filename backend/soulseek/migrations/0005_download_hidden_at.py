from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('soulseek', '0004_searchresult_is_locked')]

    operations = [
        migrations.AddField(
            model_name='download',
            name='hidden_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
