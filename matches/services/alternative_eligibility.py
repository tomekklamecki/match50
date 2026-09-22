"""Authoritative eligibility for manually managed SWAP alternatives."""
from dataclasses import dataclass

from django.db.models import QuerySet
from django.utils import timezone

from matches.models import Match, MatchAlternative, Round
from matches.services.fixture_pool import in_week


class AlternativeEligibilityError(ValueError):
    pass


@dataclass(frozen=True)
class ActiveAlternativeScope:
    round: Round
    week: object


def active_alternative_scope():
    round_ = Round.objects.filter(is_active=True).first()
    if round_ is None:
        raise AlternativeEligibilityError("There is no active Round. A manual alternative cannot be added.")
    matches = list(round_.matches.all())
    if not matches:
        raise AlternativeEligibilityError("The active Round has no Matches.")
    weeks = {match.effective_kw for match in matches}
    if len(weeks) != 1:
        raise AlternativeEligibilityError(
            "The active Round spans multiple effective ISO weeks. Align its KW values before adding alternatives."
        )
    return ActiveAlternativeScope(round_, weeks.pop())


def active_round_matches(scope=None):
    try:
        scope = scope or active_alternative_scope()
    except AlternativeEligibilityError:
        return Match.objects.none()
    return scope.round.matches.filter(swap_alternative__isnull=True).order_by("kickoff", "home_team", "pk")


def eligible_alternatives(scope=None, *, relationship=None) -> QuerySet:
    try:
        scope = scope or active_alternative_scope()
    except AlternativeEligibilityError:
        return Match.objects.none()
    consumed = MatchAlternative.objects.filter(match__round=scope.round)
    if relationship and relationship.pk:
        consumed = consumed.exclude(pk=relationship.pk)
    queryset = Match.objects.filter(
        round__isnull=True,
        status=Match.Status.UPCOMING,
        kickoff__gt=timezone.now(),
        provider_status__in=["", "NS", "TBD"],
    ).exclude(pk__in=consumed.values("alternative_id"))
    return in_week(queryset, scope.week).order_by("kickoff", "home_team", "pk")


def validate_manual_alternative(relationship, previous=None):
    scope = active_alternative_scope()
    if relationship.match.round_id != scope.round.pk:
        raise AlternativeEligibilityError("The Match must belong to the active Round.")
    if relationship.alternative_id == relationship.match_id:
        raise AlternativeEligibilityError("A Match cannot be its own alternative.")
    if not eligible_alternatives(scope, relationship=previous).filter(pk=relationship.alternative_id).exists():
        raise AlternativeEligibilityError(
            "The alternative must be an unused, unstarted Match outside the active Round in the same effective ISO week."
        )
