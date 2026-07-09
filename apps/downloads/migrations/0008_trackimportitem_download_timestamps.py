from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("downloads", "0007_trackimportitem_search_attempts"),
    ]

    operations = [
        migrations.AddField(
            model_name="trackimportitem",
            name="download_progress_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trackimportitem",
            name="download_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
