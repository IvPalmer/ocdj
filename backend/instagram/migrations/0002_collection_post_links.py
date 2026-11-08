from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('instagram', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='collection',
            name='post_links',
            field=models.JSONField(default=list),
        ),
    ]
