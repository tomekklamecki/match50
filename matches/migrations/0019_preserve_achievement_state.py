from django.db import migrations


def preserve(apps, schema_editor):
    State = apps.get_model("matches", "UserAchievement")
    Occurrence = apps.get_model("matches", "AchievementOccurrence")
    Definition = apps.get_model("matches", "Achievement")
    alias = schema_editor.connection.alias
    for state in State.objects.using(alias).select_related("achievement").iterator():
        state.current_tier = state.achievement.tier
        state.progress = max(0, state.context_data.get("progress", 0))
        state.save(using=alias, update_fields=["current_tier", "progress"])
        Occurrence.objects.using(alias).get_or_create(
            user_achievement_id=state.pk, event_key="unlock",
            defaults={"context":state.context_data, "earned_at":state.unlocked_at})
        if state.achievement.tier:
            code = state.achievement.code.rsplit("_", 1)[0]
            definition, _ = Definition.objects.using(alias).get_or_create(
                code=code, defaults={"name":code, "category":"PROGRESS", "description":""})
            parent, _ = State.objects.using(alias).get_or_create(
                user_id=state.user_id, achievement=definition,
                defaults={"progress":state.progress, "current_tier":state.current_tier,
                          "context_data":state.context_data})
            if state.progress > parent.progress:
                parent.progress = state.progress
                parent.current_tier = state.current_tier
                parent.context_data = state.context_data
                parent.save(using=alias)


class Migration(migrations.Migration):
    dependencies = [("matches", "0018_achievementoccurrence_achievement_max_stars_and_more")]
    operations = [migrations.RunPython(preserve, migrations.RunPython.noop)]
