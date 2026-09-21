import unicodedata
from django.db import migrations, models


def backfill(apps, schema_editor):
    Round = apps.get_model("matches", "Round")
    def name(value):
        return unicodedata.normalize("NFKC", value).strip().casefold()
    for round_ in Round.objects.using(schema_editor.connection.alias).prefetch_related("matches"):
        matches = list(round_.matches.all())
        if round_.is_active or (matches and all(m.status in {"FINISHED", "CANCELLED"} for m in matches)):
            matches.sort(key=lambda m: (m.kickoff, name(m.home_team), name(m.away_team), m.pk))
            round_.frozen_match_order = [m.pk for m in matches]
            round_.save(update_fields=["frozen_match_order"])


class Migration(migrations.Migration):
    dependencies = [("matches", "0021_rename_change_mind_chip")]
    operations = [
        migrations.AddField(model_name="round", name="frozen_match_order", field=models.JSONField(null=True, blank=True, editable=False)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
