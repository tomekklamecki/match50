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

    def __str__(self):
        return f"KW {self.week:02d} / {self.year}"


def current_week(now=None):
    return CalendarWeek.at(now or timezone.now())
