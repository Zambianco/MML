from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("downloads", "0006_trackimport_processing_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="trackimportitem",
            name="search_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
