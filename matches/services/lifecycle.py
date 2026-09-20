"""Single source of truth for MATCH50 round lifecycle selection."""

from matches.models import Draft, Match, Round


FINAL = {Match.Status.FINISHED, Match.Status.CANCELLED}


def get_current_round():
    return Round.objects.filter(is_active=True).first()


def is_completed_round(round_):
    matches = list(round_.matches.all())
    return bool(matches) and len({match.id for match in matches}) == round_.match_count and all(match.status in FINAL for match in matches)


def get_previous_completed_round():
    for round_ in Round.objects.prefetch_related("matches").filter(is_active=False).order_by("-ranking_date", "-id"):
        if is_completed_round(round_):
            return round_
    return None


def get_future_round_for_current_draft():
    draft = Draft.objects.filter(is_active=True).select_related("next_round").first()
    return draft.next_round if draft else None
