"""Deterministic Future ordering and immutable Round slot snapshots."""
import unicodedata

from django.db import transaction


def chronological_matches(matches):
    def name(value):
        return unicodedata.normalize("NFKC", value).strip().casefold()
    return sorted(matches, key=lambda match: (match.kickoff, name(match.home_team), name(match.away_team), match.pk))


def ordered_matches(round_, matches=None):
    matches = list(round_.matches.all() if matches is None else matches)
    if round_.frozen_match_order is None:
        return chronological_matches(matches)
    positions = {pk: index for index, pk in enumerate(round_.frozen_match_order)}
    return sorted(matches, key=lambda match: positions[match.pk])


@transaction.atomic
def freeze_match_order(round_):
    from matches.models import Round
    locked = Round.objects.select_for_update().get(pk=round_.pk)
    if locked.frozen_match_order is None:
        matches = list(locked.matches.all())
        if len(matches) != locked.match_count:
            return False
        locked.frozen_match_order = [match.pk for match in chronological_matches(matches)]
        Round.objects.filter(pk=locked.pk).update(frozen_match_order=locked.frozen_match_order)
    round_.frozen_match_order = locked.frozen_match_order
    return True
