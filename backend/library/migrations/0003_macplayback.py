from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('library', '0002_maclibrarysnapshot')]
    operations = [migrations.CreateModel(name='MacPlayback', fields=[
        ('track', models.OneToOneField(primary_key=True, serialize=False, on_delete=django.db.models.deletion.CASCADE, to='library.librarytrack')),
        ('requested_until', models.DateTimeField(null=True)),
        ('lease_token', models.CharField(max_length=64, blank=True)),
        ('lease_until', models.DateTimeField(null=True)),
        ('cache_key', models.CharField(max_length=32, blank=True)),
        ('cache_until', models.DateTimeField(null=True)),
        ('failed', models.BooleanField(default=False)),
    ])]
