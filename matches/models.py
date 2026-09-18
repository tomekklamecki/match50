from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
import random
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class Match50Season(models.Model):
    name = models.CharField(max_length=100, unique=True)
    starts_at = models.DateField()
    ends_at = models.DateField()
    is_active = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["is_active"], condition=Q(is_active=True), name="one_active_match50_season")]

    def clean(self):
        if self.ends_at < self.starts_at:
            raise ValidationError("MATCH50 season end date cannot be before its start date.")
        if self.is_active and Match50Season.objects.filter(is_active=True).exclude(pk=self.pk).exists():
            raise ValidationError("Only one MATCH50 Season can be active at a time.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Round(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=False)
    match_count = models.PositiveIntegerField(default=30)
    ranking_date = models.DateField(default=timezone.localdate)
    match50_season = models.ForeignKey("Match50Season", null=True, blank=True, on_delete=models.SET_NULL, related_name="rounds")
    active_global_modifier = models.ForeignKey("GlobalModifier", null=True, blank=True, on_delete=models.SET_NULL, related_name="active_for_rounds")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=Q(is_active=True), name="one_active_round"
            )
        ]

    def clean(self):
        if self.is_active and Round.objects.filter(is_active=True).exclude(pk=self.pk).exists():
            raise ValidationError("Only one Round can be active at a time.")
        if self.match50_season_id and not (self.match50_season.starts_at <= self.ranking_date <= self.match50_season.ends_at):
            raise ValidationError("Round ranking date must belong to its MATCH50 Season.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

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
    modifier_options = models.ManyToManyField("GlobalModifier", related_name="draft_options", blank=True)
    winning_modifier = models.ForeignKey("GlobalModifier", null=True, blank=True, on_delete=models.SET_NULL, related_name="won_drafts")
    modifier_resolved_at = models.DateTimeField(null=True, blank=True)
    modifier_resolution_method = models.CharField(max_length=20, blank=True)

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
            if now >= self.starts_at + timedelta(days=6) and self.modifier_options.count() == 3:
                self.resolve_modifier()
            if self.has_resolved_modifier():
                self.populate_next_round()

    def has_resolved_modifier(self):
        return (
            self.modifier_options.count() == 3
            and self.winning_modifier_id is not None
            and self.modifier_options.filter(pk=self.winning_modifier_id).exists()
        )

    def populate_next_round(self):
        """Assign the 30 persisted winners to one newly created Round."""
        self.validate_ready()
        if not self.has_resolved_modifier():
            raise ValidationError("Draft needs three modifier candidates and a resolved winning modifier before creating the next Round.")
        winners = list(self.pairs.select_related("winner").order_by("day_number", "id"))
        if any(pair.winner_id is None for pair in winners):
            raise ValidationError("All Draft pairs must be resolved first.")
        with transaction.atomic():
            if self.next_round_id is None:
                self.next_round = Round.objects.create(
                    name=self.next_round_name, is_active=False, match_count=30, active_global_modifier=self.winning_modifier
                )
                self.is_active = False
                self.save(update_fields=["next_round", "is_active"])
            Match.objects.filter(pk__in=[pair.winner_id for pair in winners]).update(round=self.next_round)
        return self.next_round

    def resolve_modifier(self):
        if self.winning_modifier_id:
            return self.winning_modifier
        options = list(self.modifier_options.all())
        if len(options) != 3:
            raise ValidationError("Draft needs exactly three modifier candidates.")
        counts = {item.id: self.modifier_votes.filter(selected_modifier=item).count() for item in options}
        high = max(counts.values())
        leaders = [item for item in options if counts[item.id] == high]
        self.winning_modifier = random.choice(leaders)
        self.modifier_resolution_method = "VOTE" if len(leaders) == 1 else "RANDOM_TIEBREAK"
        self.modifier_resolved_at = timezone.now()
        self.save(update_fields=["winning_modifier", "modifier_resolution_method", "modifier_resolved_at"])
        return self.winning_modifier


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    short_name = models.CharField(max_length=30, blank=True)
    active = models.BooleanField(default=True)
    def __str__(self): return self.name


class Competition(models.Model):
    code = models.CharField(max_length=12, unique=True)
    name = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    def __str__(self): return self.name


class CompetitionSeason(models.Model):
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="seasons")
    season_label = models.CharField(max_length=20)
    champion_team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="champion_seasons")
    active = models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["competition","season_label"],name="unique_competition_season")]
    def __str__(self): return f"{self.competition} {self.season_label}"


class GlobalModifier(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    enabled = models.BooleanField(default=True)
    def __str__(self): return self.name


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
    competition_season = models.ForeignKey("CompetitionSeason", null=True, blank=True, on_delete=models.SET_NULL, related_name="matches")
    home_team_entity = models.ForeignKey("Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="home_matches")
    away_team_entity = models.ForeignKey("Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="away_matches")
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

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(home_goals__isnull=True, away_goals__isnull=True) | Q(home_goals__isnull=False, away_goals__isnull=False)),
                name="match_score_is_complete_or_empty",
            )
        ]

    def clean(self):
        if (self.home_goals is None) != (self.away_goals is None):
            raise ValidationError("A final result requires both home and away goals.")

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = Match.objects.filter(pk=self.pk).values_list(
                "home_goals", "away_goals", "status", "result"
            ).first()
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

        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"status", "result"}
        self.full_clean()
        super().save(*args, **kwargs)
        current = (self.home_goals, self.away_goals, self.status, self.result)
        if previous != current:
            def recalculate_affected_scores(match_id=self.pk, round_id=self.round_id):
                from matches.services.scoring import recalculate_round_scores
                round_ids = set()
                if round_id:
                    round_ids.add(round_id)
                round_ids.update(
                    ChipAssignment.objects.filter(replacement_match_id=match_id).values_list("round_id", flat=True)
                )
                for affected_round_id in round_ids:
                    recalculate_round_scores(Round.objects.get(pk=affected_round_id))
            transaction.on_commit(recalculate_affected_scores)

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
        with transaction.atomic():
            self.full_clean()
            super().save(*args, **kwargs)
            if self.match_a_id and self.match_b_id:
                DraftPairCandidate.objects.filter(pair=self).exclude(match_id__in=[self.match_a_id, self.match_b_id]).delete()
                DraftPairCandidate.objects.update_or_create(draft=self.draft, match_id=self.match_a_id, defaults={"pair": self, "side": "A"})
                DraftPairCandidate.objects.update_or_create(draft=self.draft, match_id=self.match_b_id, defaults={"pair": self, "side": "B"})

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


class DraftPairCandidate(models.Model):
    """Normalized, unique membership of a candidate in a Draft."""
    draft = models.ForeignKey(Draft, on_delete=models.CASCADE, related_name="candidate_memberships")
    pair = models.ForeignKey(DraftPair, on_delete=models.CASCADE, related_name="candidate_memberships")
    match = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="draft_candidate_memberships")
    side = models.CharField(max_length=1, choices=[("A", "A"), ("B", "B")])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["draft", "match"], name="one_candidate_per_draft"),
            models.UniqueConstraint(fields=["pair", "side"], name="one_candidate_per_pair_side"),
        ]

    def clean(self):
        if self.pair_id and self.draft_id != self.pair.draft_id:
            raise ValidationError("Candidate membership must use the pair's Draft.")


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


class DraftModifierVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    draft = models.ForeignKey(Draft, on_delete=models.CASCADE, related_name="modifier_votes")
    selected_modifier = models.ForeignKey(GlobalModifier, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["user","draft"],name="one_modifier_vote_per_draft")]
    def clean(self):
        if not self.draft.modifier_options.filter(pk=self.selected_modifier_id).exists():
            raise ValidationError("Modifier must be a Draft candidate.")
        if self.pk or timezone.now() >= self.draft.starts_at + timedelta(days=6):
            raise ValidationError("Modifier voting is closed.")
    def save(self,*args,**kwargs):
        self.full_clean(); super().save(*args,**kwargs)


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


class ChipAssignment(models.Model):
    class Chip(models.TextChoices):
        BANKER = "BANKER", "Banker"
        DOUBLE_PICK = "DOUBLE_PICK", "Double Pick"
        CHANGE_MIND = "CHANGE_MIND", "I've Changed My Mind"
        SWAP = "SWAP", "Swap"
        GOOOOOOOAL = "GOOOOOOOOAL", "Goooooooal!"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="chip_assignments")
    chip = models.CharField(max_length=16, choices=Chip.choices)
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="chip_assignments")
    outcomes = models.JSONField(default=list, blank=True)
    goal_team = models.CharField(max_length=100, blank=True)
    replacement_match = models.ForeignKey(Match, null=True, blank=True, on_delete=models.PROTECT, related_name="swap_replacements")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "round", "chip"], name="one_chip_type_per_round"),
            models.UniqueConstraint(fields=["user", "round", "match"], name="one_chip_per_match_round"),
        ]

    @property
    def assignment_editable(self):
        return timezone.now() < self.match.kickoff

    @property
    def prediction_editable(self):
        if self.chip == self.Chip.CHANGE_MIND:
            return timezone.now() < self.match.kickoff + timedelta(minutes=60)
        return timezone.now() < self.match.kickoff

    def clean(self):
        if self.match.round_id != self.round_id:
            raise ValidationError("Chip match must belong to its Round.")
        if self._state.adding and not self.assignment_editable:
            raise ValidationError("Chip assignments cannot be created at or after kickoff.")
        if self.pk and not self.assignment_editable:
            previous = ChipAssignment.objects.get(pk=self.pk)
            if (previous.chip, previous.match_id, previous.outcomes, previous.goal_team, previous.replacement_match_id) != (self.chip, self.match_id, self.outcomes, self.goal_team, self.replacement_match_id):
                raise ValidationError("Chip assignment is locked at kickoff.")
        if self.chip == self.Chip.DOUBLE_PICK and set(self.outcomes) not in ({"1", "X"}, {"1", "2"}, {"X", "2"}):
            raise ValidationError("Double Pick requires exactly two different outcomes.")
        if self.chip == self.Chip.GOOOOOOOAL and self.goal_team not in {self.match.home_team, self.match.away_team}:
            raise ValidationError("Choose one team from this match for Goooooooal.")
        if self.chip == self.Chip.SWAP:
            pair = DraftPair.objects.filter(winner=self.match, resolved_at__isnull=False).first()
            if not pair:
                raise ValidationError("Swap requires a persisted Draft loser for this match.")
            loser_id = pair.match_b_id if pair.match_a_id == self.match_id else pair.match_a_id
            if self.replacement_match_id != loser_id or timezone.now() >= self.replacement_match.kickoff:
                raise ValidationError("Swap replacement must be the unstarted loser of this Draft pair.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class UserRoundScore(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="round_scores")
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="user_scores")
    typy_points = models.IntegerField(default=0)
    gole_points = models.IntegerField(default=0)
    bonus_points = models.IntegerField(default=0)
    total_points = models.IntegerField(default=0)
    breakdown = models.JSONField(default=list, blank=True)
    calculated_at = models.DateTimeField(auto_now=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["user","round"],name="one_user_round_score")]
    def save(self,*args,**kwargs):
        self.total_points=self.typy_points+self.gole_points+self.bonus_points
        # update_or_create supplies update_fields.  Include the derived total
        # explicitly so it is persisted rather than only updated in memory.
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"total_points"}
        super().save(*args,**kwargs)
