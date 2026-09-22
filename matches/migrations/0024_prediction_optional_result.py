from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("matches", "0023_football_foundation")]

    operations = [
        migrations.AlterField(
            model_name="prediction",
            name="predicted_result",
            field=models.CharField(
                blank=True,
                choices=[("1", "1"), ("X", "X"), ("2", "2")],
                default="",
                max_length=1,
            ),
        ),
    ]
