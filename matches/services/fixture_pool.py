from django.db.models import Q
from django.utils import timezone
from matches.models import Match


def in_week(queryset, week):
    return queryset.filter(Q(kw_override_year=week.year, kw_override_week=week.week) |
        Q(kw_override_year__isnull=True, automatic_kw_year=week.year, automatic_kw_week=week.week))


def draft_candidates(week=None):
    pool = Match.objects.filter(round__isnull=True, status=Match.Status.UPCOMING, kickoff__gt=timezone.now(),
        provider_status__in=["", "NS", "TBD"]).exclude(draft_candidate_memberships__isnull=False).exclude(alternative_for__isnull=False)
    return (in_week(pool, week) if week else pool).order_by("kickoff", "home_team", "pk")
