"""Football scheduling buckets, independent of provider matchday labels."""
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone


@dataclass(frozen=True, order=True)
class CalendarWeek:
    year: int
    week: int

    def __post_init__(self):
        date.fromisocalendar(self.year, self.week, 1)

    @classmethod
    def at(cls, instant):
        if timezone.is_naive(instant):
            raise ValueError("Kickoff must include a timezone.")
        value = instant.astimezone(ZoneInfo(settings.FOOTBALL_TIME_ZONE)).isocalendar()
        return cls(value.year, value.week)

    def shift(self, weeks=1):
        value = (date.fromisocalendar(self.year, self.week, 1)+timedelta(weeks=weeks)).isocalendar()
        return CalendarWeek(value.year, value.week)

    @property
    def date_range_label(self):
        monday = date.fromisocalendar(self.year, self.week, 1)
        sunday = monday + timedelta(days=6)
        return f"{monday:%d.%m}–{sunday:%d.%m}"

    def __str__(self):
        return f"KW {self.week:02d} / {self.year}"


def current_week(now=None):
    return CalendarWeek.at(now or timezone.now())


def round_week_label(round_):
    """Use the round's fixture KW overrides, never its name or today's date.

    Legacy rounds may span several weeks; display each rather than inventing
    a single week. Original round slots keep this label independent of SWAP.
    """
    if round_ is None:
        return ""
    weeks = sorted({match.effective_kw for match in round_.matches.all()})
    return " · ".join(week.date_range_label for week in weeks)
