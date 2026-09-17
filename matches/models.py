from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
import random
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class Round(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=False)
    match_count = models.PositiveIntegerField(default=30)

    def __str__(self):
        return self.name


class Draft(models.Model):
    """A six-day community selection process for one upcoming round."""

    name = models.CharField(max_length=100)
    starts_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    next_round_name = models.CharField(max_length=100, default="Następna runda")
    next_round = models.OneToOneField(
        Round, null=True, blank=True, on_delete=models.SET_NULL, related_name="source_draft"
    )

    class Meta:
        ordering = ["-starts_at", "-id"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.is_active and Draft.objects.filter(is_active=True).exclude(pk=self.pk).exists():
            raise ValidationError("Only one Draft can be active at a time.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def validate_ready(self):
        pairs = list(self.pairs.all())
        if len(pairs) != 30:
            raise ValidationError("Draft must contain exactly 30 pairs.")
        if {pair.day_number for pair in pairs} != set(range(1, 7)):
            raise ValidationError("Draft must contain pairs for all 6 days.")
        if any(sum(pair.day_number == day for pair in pairs) != 5 for day in range(1, 7)):
            raise ValidationError("Each Draft day must contain exactly 5 pairs.")
        candidates = [match_id for pair in pairs for match_id in (pair.match_a_id, pair.match_b_id)]
        if len(set(candidates)) != 60:
            raise ValidationError("Draft must use exactly 60 unique candidate matches.")

    def resolve_closed_pairs(self, now=None):
        now = now or timezone.now()
        for pair in self.pairs.select_for_update().filter(resolved_at__isnull=True):
            if now >= pair.closes_at:
                pair.resolve()
        if self.pairs.count() == 30 and not self.pairs.filter(resolved_at__isnull=True).exists():
            self.populate_next_round()

    def populate_next_round(self):
        """Assign the 30 persisted winners to one newly created Round."""
        self.validate_ready()
        winners = list(self.pairs.select_related("winner").order_by("day_number", "id"))
        if any(pair.winner_id is None for pair in winners):
            raise ValidationError("All Draft pairs must be resolved first.")
        with transaction.atomic():
            if self.next_round_id is None:
                self.next_round = Round.objects.create(
                    name=self.next_round_name, is_active=False, match_count=30
                )
                self.is_active = False
                self.save(update_fields=["next_round", "is_active"])
            Match.objects.filter(pk__in=[pair.winner_id for pair in winners]).update(round=self.next_round)
        return self.next_round


class Match(models.Model):
    class Status(models.TextChoices):
        UPCOMING = "UPCOMING", "Upcoming"
        LIVE = "LIVE", "Live"
        FINISHED = "FINISHED", "Finished"
        CANCELLED = "CANCELLED", "Cancelled"

    round = models.ForeignKey(
        Round,
        on_delete=models.CASCADE,
        related_name="matches",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPCOMING)

    @property
    def current_status(self):
        if self.status in {self.Status.FINISHED, self.Status.CANCELLED}:
            return self.status
        return self.Status.LIVE if timezone.now() >= self.kickoff else self.Status.UPCOMING

    @property
    def predictions_editable(self):
        return self.current_status == self.Status.UPCOMING and timezone.now() < self.kickoff

    league = models.CharField(max_length=100)
    home_team = models.CharField(max_length=100)
    away_team = models.CharField(max_length=100)

    kickoff = models.DateTimeField()

    home_goals = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
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
        blank=True,
        editable=False,
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

        if self.home_goals is not None and self.away_goals is not None:
            self.status = self.Status.FINISHED

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.home_team} - {self.away_team}"


class DraftPair(models.Model):
    class Resolution(models.TextChoices):
        VOTE = "VOTE", "Vote"
        RANDOM_TIE = "RANDOM_TIE", "Random tie draw"

    draft = models.ForeignKey(Draft, on_delete=models.CASCADE, related_name="pairs")
    day_number = models.PositiveSmallIntegerField(null=True, blank=True)
    match_a = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="draft_pairs_as_a")
    match_b = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="draft_pairs_as_b")
    winner = models.ForeignKey(
        Match, null=True, blank=True, on_delete=models.PROTECT, related_name="won_draft_pairs"
    )
    resolution_method = models.CharField(max_length=12, choices=Resolution.choices, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["day_number", "id"]
        constraints = [
            models.CheckConstraint(condition=~Q(match_a=models.F("match_b")), name="draft_pair_distinct_matches"),
            models.UniqueConstraint(fields=["draft", "match_a"], name="draft_pair_unique_match_a"),
            models.UniqueConstraint(fields=["draft", "match_b"], name="draft_pair_unique_match_b"),
        ]

    @property
    def opens_at(self):
        return self.draft.starts_at + timedelta(days=self.day_number - 1)

    @property
    def closes_at(self):
        return self.draft.starts_at + timedelta(days=self.day_number)

    @property
    def is_open(self):
        now = timezone.now()
        return self.opens_at <= now < self.closes_at and self.resolved_at is None

    def clean(self):
        errors = {}
        if self.day_number is not None and not 1 <= self.day_number <= 6:
            errors["day_number"] = "Draft day must be between 1 and 6."
        if self.match_a_id == self.match_b_id:
            errors["match_b"] = "A pair needs two different matches."
        if self.match_a_id and self.match_b_id:
            if self.draft_id:
                used = DraftPair.objects.filter(draft_id=self.draft_id).exclude(pk=self.pk).filter(
                    Q(match_a_id__in=[self.match_a_id, self.match_b_id]) | Q(match_b_id__in=[self.match_a_id, self.match_b_id])
                )
                if used.exists():
                    errors["match_a"] = "A candidate match can belong to only one pair in a Draft."
            if Match.objects.filter(pk__in=[self.match_a_id, self.match_b_id], round__is_active=True).exists():
                errors["match_a"] = "Matches from the active prediction round cannot be Draft candidates."
        if self.draft_id:
            siblings = DraftPair.objects.filter(draft_id=self.draft_id).exclude(pk=self.pk)
            if siblings.count() >= 30:
                errors["draft"] = "A Draft can contain at most 30 pairs."
            if siblings.filter(day_number=self.day_number).count() >= 5:
                errors["day_number"] = "A Draft day can contain at most 5 pairs."
        if self.pk and DraftPair.objects.filter(pk=self.pk, resolved_at__isnull=False).exists():
            previous = DraftPair.objects.get(pk=self.pk)
            if (previous.day_number, previous.match_a_id, previous.match_b_id) != (self.day_number, self.match_a_id, self.match_b_id):
                errors["__all__"] = "Resolved pairs cannot be modified."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self._state.adding and self.draft_id and self.day_number is None:
            pair_count = DraftPair.objects.filter(draft_id=self.draft_id).count()
            if pair_count >= 30:
                raise ValidationError("A Draft can contain exactly 30 pairs.")
            self.day_number = pair_count // 5 + 1
        elif self._state.adding and self.draft_id and DraftPair.objects.filter(draft_id=self.draft_id).count() >= 30:
            raise ValidationError("A Draft can contain exactly 30 pairs.")
        self.full_clean()
        super().save(*args, **kwargs)

    def vote_counts(self):
        return {
            self.match_a_id: self.votes.filter(selected_match_id=self.match_a_id).count(),
            self.match_b_id: self.votes.filter(selected_match_id=self.match_b_id).count(),
        }

    def percentages(self):
        counts = self.vote_counts()
        total = sum(counts.values())
        if not total:
            return {self.match_a_id: 0, self.match_b_id: 0}
        return {match_id: round(count * 100 / total) for match_id, count in counts.items()}

    def resolve(self):
        if timezone.now() < self.closes_at:
            raise ValidationError("A pair can only be resolved after it closes.")
        with transaction.atomic():
            pair = DraftPair.objects.select_for_update().get(pk=self.pk)
            if pair.resolved_at:
                return pair
            counts = pair.vote_counts()
            if counts[pair.match_a_id] == counts[pair.match_b_id]:
                pair.winner_id = random.choice([pair.match_a_id, pair.match_b_id])
                pair.resolution_method = self.Resolution.RANDOM_TIE
            else:
                pair.winner_id = max(counts, key=counts.get)
                pair.resolution_method = self.Resolution.VOTE
            pair.resolved_at = timezone.now()
            pair.save(update_fields=["winner", "resolution_method", "resolved_at"])
            self.winner_id = pair.winner_id
            self.resolution_method = pair.resolution_method
            self.resolved_at = pair.resolved_at
            return self


class DraftVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    pair = models.ForeignKey(DraftPair, on_delete=models.CASCADE, related_name="votes")
    selected_match = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="draft_votes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "pair"], name="one_draft_vote_per_user_pair")]

    def clean(self):
        if self.selected_match_id not in {self.pair.match_a_id, self.pair.match_b_id}:
            raise ValidationError("Vote must select a match from this pair.")
        if not self.pair.is_open:
            raise ValidationError("This pair is not open for voting.")
        if self.pk:
            raise ValidationError("Draft votes cannot be changed.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


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

    total_goals = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(15)],
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
