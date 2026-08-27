from django.db import migrations


class Migration(migrations.Migration):
    """Drop `batch_limit`, added one commit earlier and already obsolete.

    It described a per-cycle cap on how many lists the Mac would fetch. A cycle
    now drains the queue instead, so the number no longer caps anything and the
    panel does not read it. Better a short-lived column than a field nothing
    means.
    """

    dependencies = [
        ('traxdb', '0006_scrapedfolder_last_error_macinventory_cadence'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='macinventory',
            name='batch_limit',
        ),
    ]
