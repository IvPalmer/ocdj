from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ytfetch', '0002_fetchjob_needs_local'),
    ]

    operations = [
        migrations.AddField(
            model_name='fetchjob',
            name='abr',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fetchjob',
            name='duration',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fetchjob',
            name='ext',
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
