from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("downloads", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trackimportitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pendente"),
                    ("searching", "Buscando"),
                    ("downloading", "Baixando"),
                    ("done", "Concluido"),
                    ("error", "Erro"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="trackimportitem",
            name="download_progress",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="trackimportitem",
            name="download_path",
            field=models.CharField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name="trackimportitem",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
