from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ytfetch', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='fetchjob',
            name='status',
            field=models.CharField(choices=[('queued', 'Queued'), ('fetching', 'Fetching'), ('needs_local', 'Needs local download'), ('downloaded', 'Downloaded'), ('failed', 'Failed')], default='queued', max_length=20),
        ),
    ]
