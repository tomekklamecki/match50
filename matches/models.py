from django.conf import settings
from django.db import models


class Round(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Match(models.Model):
    round = models.ForeignKey(
        Round,
        on_delete=models.CASCADE,
        related_name="matches",
        null=True,
        blank=True
    )

    league = models.CharField(max_length=100)
    home_team = models.CharField(max_length=100)
    away_team = models.CharField(max_length=100)

    kickoff = models.DateTimeField()

    home_goals = models.PositiveSmallIntegerField(
        null=True,
        blank=True
    )

    away_goals = models.PositiveSmallIntegerField(
        null=True,
        blank=True
    )

    result = models.CharField(
        max_length=1,
        choices=[
            ("1", "1"),
            ("X", "X"),
            ("2", "2"),
        ],
        null=True,
        blank=True
    )

    def save(self, *args, **kwargs):
        if self.home_goals is not None and self.away_goals is not None:
            if self.home_goals > self.away_goals:
                self.result = "1"
            elif self.home_goals < self.away_goals:
                self.result = "2"
            else:
                self.result = "X"
        else:
            self.result = None

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.home_team} - {self.away_team}"


class Prediction(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )

    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE
    )

    predicted_result = models.CharField(
        max_length=1,
        choices=[
            ("1", "1"),
            ("X", "X"),
            ("2", "2"),
        ]
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "match"],
                name="unique_user_match_prediction"
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.match} - {self.predicted_result}"