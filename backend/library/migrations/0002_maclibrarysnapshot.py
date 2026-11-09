from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('library', '0001_initial')]
    operations = [
        migrations.AlterField(
            model_name='librarytrack', name='source',
            field=models.CharField(choices=[('organize_pipeline', 'Organize Pipeline'),
                ('traxdb', 'TraxDB'), ('manual', 'Manual'),
                ('mac_snapshot', 'Mac Metadata Snapshot')], default='organize_pipeline', max_length=20)),
        migrations.CreateModel(
            name='MacLibrarySnapshot', fields=[
                ('collection_id', models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ('captured_at', models.CharField(blank=True, max_length=27)),
                ('digest', models.CharField(blank=True, max_length=64)),
                ('imported_at', models.DateTimeField(auto_now=True)),
            ]),
    ]
