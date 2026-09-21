"""Single source of truth for MATCH50 round lifecycle selection."""

from matches.models import Draft, Match, Round


FINAL = {Match.Status.FINISHED, Match.Status.CANCELLED}


def promote_round(round_):
    """Activate a complete Future round without changing its slots or picks."""
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from matches.services.match_order import freeze_match_order
    with transaction.atomic():
        locked = Round.objects.select_for_update().get(pk=round_.pk)
        if not freeze_match_order(locked):
            raise ValidationError("A complete Round is required before promotion.")
        Round.objects.filter(is_active=True).exclude(pk=locked.pk).update(is_active=False)
        locked.is_active = True
        locked.save(update_fields=["is_active"])
        round_.is_active = True
        round_.frozen_match_order = locked.frozen_match_order
    return round_


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
