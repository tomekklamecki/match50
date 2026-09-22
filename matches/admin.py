from django import forms
from django.contrib import admin
from django.db.models import Q
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils import timezone

from .models import Competition, CompetitionSeason, Draft, DraftModifierVote, DraftPair, DraftVote, GlobalModifier, Match, Match50Season, Prediction, Round, Team, MatchAlternative
from .services.calendar_week import CalendarWeek, current_week
from .services.fixture_pool import draft_candidates, in_week
from .services.alternative_eligibility import (
    AlternativeEligibilityError,
    active_alternative_scope,
    active_round_matches,
    eligible_alternatives,
)


def draft_fixture_label(match):
    competition = match.competition_season.competition.name if match.competition_season_id else match.league
    kickoff = timezone.localtime(match.kickoff)
    return f"{competition} | {kickoff:%a %d.%m %H:%M} | {match.home_team} – {match.away_team} | {match.effective_kw}"


class DraftFixtureChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, match):
        return draft_fixture_label(match)


@admin.register(Draft)
class DraftAdmin(admin.ModelAdmin):
    change_list_template = "admin/matches/football_week_list.html"
    list_display = ("name", "starts_at", "is_active", "pair_count", "next_round")
    list_filter = ("is_active",)

    def changelist_view(self, request, extra_context=None):
        week = current_week()
        return super().changelist_view(request, {**(extra_context or {}), "current_kw": week, "suggested_kw": week.shift()})

    @admin.display(description="Pairs")
    def pair_count(self, obj):
        return obj.pairs.count()


@admin.register(DraftPair)
class DraftPairAdmin(admin.ModelAdmin):
    list_display = ("pair", "draft", "day_number", "effective_weeks", "competitions", "winner", "resolution_method", "resolved_at")
    list_filter = ("draft", "day_number", "resolution_method")
    list_select_related = ("draft", "match_a__competition_season__competition", "match_b__competition_season__competition", "winner")
    search_fields = ("match_a__home_team", "match_a__away_team", "match_b__home_team", "match_b__away_team")
    readonly_fields = ("winner", "resolution_method", "resolved_at")
    fields = (
        "draft", "day_number", "kw_filter", "competition_filter", "fixture_search",
        "match_a", "match_b", "winner", "resolution_method", "resolved_at",
    )

    @staticmethod
    def fixture_label(match):
        return draft_fixture_label(match)

    class PairForm(forms.ModelForm):
        kw_filter = forms.ChoiceField(required=False, label="Effective KW", choices=(("", "Choose KW…"),))
        competition_filter = forms.ModelChoiceField(
            required=False, label="Competition", queryset=Competition.objects.all().order_by("name"),
            empty_label="All competitions",
        )
        fixture_search = forms.CharField(
            required=False, label="Search fixtures",
            widget=forms.TextInput(attrs={"placeholder": "Team or competition", "autocomplete": "off"}),
        )
        match_a = DraftFixtureChoiceField(queryset=Match.objects.none(), label="Match A", widget=forms.Select(attrs={"size": 12}))
        match_b = DraftFixtureChoiceField(queryset=Match.objects.none(), label="Match B", widget=forms.Select(attrs={"size": 12}))
        day_number = forms.IntegerField(
            required=False,
            min_value=1,
            max_value=6,
            widget=forms.NumberInput(attrs={"min": 1, "max": 6, "step": 1}),
        )

        class Meta:
            model = DraftPair
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["day_number"].disabled = False
            self.fields["day_number"].widget.attrs.pop("readonly", None)
            self.fields["day_number"].widget.attrs.pop("disabled", None)
            self.fields["day_number"].help_text = "Po wybraniu Draftu podpowiadany automatycznie; możesz go zmienić ręcznie."
            selected_ids = {self.instance.match_a_id, self.instance.match_b_id}
            selected_ids.update(self.data.get(name) for name in ("match_a", "match_b") if self.data.get(name))
            selected_ids.discard(None)
            selected_ids.discard("")
            selected = Match.objects.filter(pk__in=selected_ids).select_related("competition_season__competition")
            self.fields["match_a"].queryset = selected
            self.fields["match_b"].queryset = selected
            week_rows = draft_candidates().values_list(
                "automatic_kw_year", "automatic_kw_week", "kw_override_year", "kw_override_week"
            )
            weeks = sorted({
                (override_year or year, override_week or week)
                for year, week, override_year, override_week in week_rows if year and week
            })
            self.fields["kw_filter"].choices = [
                ("", "Choose KW…"),
                *((f"{year}-{week}", str(CalendarWeek(year, week))) for year, week in weeks),
            ]
            for name in ("match_a", "match_b"):
                self.fields[name].help_text = "Select one fixture from the visible filtered candidate pool."
    form = PairForm

    def get_urls(self):
        return [
            path("available-candidates/", self.admin_site.admin_view(self.available_candidates), name="matches_draftpair_available_candidates"),
        ] + super().get_urls()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        endpoint = reverse("admin:matches_draftpair_available_candidates")
        for field_name in ("match_a", "match_b"):
            form.base_fields[field_name].widget.attrs["data-candidate-url"] = endpoint
            form.base_fields[field_name].widget.attrs["data-current-pair"] = obj.pk if obj else ""
        form.base_fields["day_number"].widget.attrs["data-auto-day"] = "true" if obj is None else "false"
        return form

    def available_candidates(self, request):
        draft_id = request.GET.get("draft")
        current_pair_id = request.GET.get("current_pair")
        if not draft_id:
            return JsonResponse({"candidates": [], "weeks": [], "competitions": []})
        pairs = DraftPair.objects.filter(draft_id=draft_id)
        if current_pair_id:
            pairs = pairs.exclude(pk=current_pair_id)
        used_ids = {match_id for match_ids in pairs.values_list("match_a_id", "match_b_id") for match_id in match_ids}
        candidates = draft_candidates().exclude(pk__in=used_ids).select_related("competition_season__competition")
        if current_pair_id:
            current_ids = DraftPair.objects.filter(pk=current_pair_id, draft_id=draft_id).values_list("match_a_id", "match_b_id").first()
            if current_ids:
                candidates = Match.objects.filter(Q(pk__in=current_ids) | Q(pk__in=candidates.values("pk"))).select_related("competition_season__competition").order_by("kickoff", "home_team", "pk")

        metadata_rows = list(candidates.values_list(
            "automatic_kw_year", "automatic_kw_week", "kw_override_year", "kw_override_week",
            "competition_season__competition_id", "competition_season__competition__name",
        ))
        weeks = sorted({(override_year or year, override_week or week) for year, week, override_year, override_week, _, _ in metadata_rows if year and week})
        competitions = sorted({(competition_id, name) for *_, competition_id, name in metadata_rows if competition_id}, key=lambda item: item[1])
        week_value = request.GET.get("week", "")
        competition_value = request.GET.get("competition", "")
        query = request.GET.get("q", "").strip()
        if week_value:
            try:
                year, week = map(int, week_value.split("-"))
                candidates = in_week(candidates, CalendarWeek(year, week))
            except (TypeError, ValueError):
                candidates = candidates.none()
        if competition_value:
            candidates = candidates.filter(competition_season__competition_id=competition_value)
        if query:
            candidates = candidates.filter(Q(home_team__icontains=query) | Q(away_team__icontains=query) | Q(league__icontains=query) | Q(competition_season__competition__name__icontains=query))
        if not (week_value or competition_value or query or current_pair_id):
            candidates = candidates.none()
        candidates = list(candidates[:250])
        pair_count = DraftPair.objects.filter(draft_id=draft_id).count()
        return JsonResponse({
            "next_day": min(pair_count // 5 + 1, 6),
            "weeks": [{"value": f"{year}-{week}", "text": str(CalendarWeek(year, week))} for year, week in weeks],
            "competitions": [{"id": pk, "text": name} for pk, name in competitions],
            "candidates": [{"id": match.id, "text": self.fixture_label(match)} for match in candidates],
            "truncated": len(candidates) == 250,
        })

    @admin.display(description="Pair")
    def pair(self, obj):
        return f"{obj.match_a} ↔ {obj.match_b}"

    @admin.display(description="Effective KW")
    def effective_weeks(self, obj):
        return " / ".join(sorted({str(obj.match_a.effective_kw), str(obj.match_b.effective_kw)}))

    @admin.display(description="Competition")
    def competitions(self, obj):
        def name(match):
            return match.competition_season.competition.name if match.competition_season_id else match.league
        return " / ".join(sorted({name(obj.match_a), name(obj.match_b)}))

    class Media:
        # Versioned filename prevents an older DOM-only picker script from
        # surviving a deploy in the browser's static-file cache.
        js = ("matches/draft_pair_picker_v2.js",)
        css = {"all": ("matches/draft_pair_admin.css",)}


@admin.register(DraftVote)
class DraftVoteAdmin(admin.ModelAdmin):
    list_display = ("pair", "user", "selected_match", "created_at")
    readonly_fields = ("pair", "user", "selected_match", "created_at")


class EffectiveWeekFilter(admin.SimpleListFilter):
    title = "Effective KW"
    parameter_name = "effective_kw"

    def lookups(self, request, model_admin):
        weeks = set()
        for year, week, override_year, override_week in model_admin.get_queryset(request).values_list("automatic_kw_year", "automatic_kw_week", "kw_override_year", "kw_override_week"):
            if year and week:
                weeks.add((override_year or year, override_week or week))
        return [(f"{year}-{week}", str(CalendarWeek(year, week))) for year, week in sorted(weeks)]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        try:
            year, week = map(int, self.value().split("-"))
            return in_week(queryset, CalendarWeek(year, week))
        except (ValueError, TypeError):
            return queryset.none()


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    change_list_template = "admin/matches/football_week_list.html"
    list_display = ("__str__", "api_football_id", "competition_season", "kickoff", "automatic_week", "effective_week", "has_kw_override", "provider_status", "status", "round")
    list_filter = ("competition_season__competition", "competition_season__provider_season", EffectiveWeekFilter, ("kw_override_year", admin.EmptyFieldListFilter), "provider_status", "round")
    search_fields = ("home_team", "away_team", "=api_football_id")
    list_select_related = ("competition_season__competition", "round")
    readonly_fields = ("automatic_week", "effective_week", "provider_status", "provider_score", "provider_synced_at")
    date_hierarchy = "kickoff"
    actions = ["bootstrap_round"]

    @admin.display(description="Automatic KW")
    def automatic_week(self, obj):
        return str(obj.automatic_kw) if obj.kickoff else "—"

    @admin.display(description="Effective KW")
    def effective_week(self, obj):
        return str(obj.effective_kw) if obj.kickoff else "—"

    @admin.display(boolean=True, description="KW override")
    def has_kw_override(self, obj):
        return obj.kw_override_year is not None

    def changelist_view(self, request, extra_context=None):
        week = current_week()
        return super().changelist_view(request, {**(extra_context or {}), "current_kw": week, "suggested_kw": week.shift()})

    @admin.action(description="Create initial inactive Round from exactly 30 selected fixtures")
    def bootstrap_round(self, request, queryset):
        from django.db import transaction
        from .services.fixture_pool import draft_candidates
        with transaction.atomic():
            ids = list(queryset.select_for_update().values_list("pk", flat=True))
            if len(ids) != 30 or draft_candidates().filter(pk__in=ids).count() != 30:
                self.message_user(request, "Select exactly 30 unassigned, unstarted fixtures outside Draft pairs/alternatives.", level="error")
                return
            round_ = Round.objects.create(name="Initial football Round", match_count=30)
            Match.objects.filter(pk__in=ids).update(round=round_)
        self.message_user(request, f"Created Round #{round_.pk}. Add alternatives, then activate from Rounds.")


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = ("name", "ranking_date", "is_active", "match_count")
    readonly_fields = ("frozen_match_order", "team_history_requested_at", "team_history_synced_at")
    actions = ["activate_round"]

    @admin.action(description="Promote selected complete Round and freeze match order")
    def activate_round(self, request, queryset):
        from django.core.exceptions import ValidationError
        from .services.lifecycle import promote_round
        if queryset.count() != 1:
            self.message_user(request, "Select one complete Round.", level="error")
            return
        try:
            promote_round(queryset.get())
        except ValidationError as error:
            self.message_user(request, "; ".join(error.messages), level="error")
            return
        self.message_user(request, "Round activated; match order frozen. Team history requested: run refresh_active_round_team_history now (and nightly).")


class MatchAlternativeForm(forms.ModelForm):
    class Meta:
        model = MatchAlternative
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope_error = None
        if self.instance.pk:
            return
        try:
            scope = active_alternative_scope()
        except AlternativeEligibilityError as error:
            self.scope_error = str(error)
            self.fields["match"].queryset = Match.objects.none()
            self.fields["alternative"].queryset = Match.objects.none()
            self.fields["match"].help_text = self.scope_error
            self.fields["alternative"].help_text = self.scope_error
        else:
            self.fields["match"].queryset = active_round_matches(scope)
            self.fields["alternative"].queryset = eligible_alternatives(scope)
            self.fields["match"].help_text = f"Matches in active Round: {scope.round}."
            self.fields["alternative"].help_text = f"Unused eligible fixtures in {scope.week}."

    def clean(self):
        cleaned = super().clean()
        if self.scope_error:
            raise forms.ValidationError(self.scope_error)
        return cleaned


class AlternativeWeekFilter(admin.SimpleListFilter):
    title = "Effective KW"
    parameter_name = "effective_kw"

    def lookups(self, request, model_admin):
        weeks = {match.effective_kw for match in Match.objects.filter(swap_alternative__isnull=False)}
        return [(f"{week.year}-{week.week}", str(week)) for week in sorted(weeks)]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        try:
            year, week = map(int, self.value().split("-"))
            match_ids = in_week(Match.objects.all(), CalendarWeek(year, week)).values("pk")
            return queryset.filter(match_id__in=match_ids)
        except (ValueError, TypeError):
            return queryset.none()


class AlternativeSourceFilter(admin.SimpleListFilter):
    title = "Source"
    parameter_name = "source"

    def lookups(self, request, model_admin):
        return (("draft", "DRAFT"), ("manual", "MANUAL"))

    def queryset(self, request, queryset):
        if self.value() == "draft":
            return queryset.filter(source_pair__isnull=False)
        if self.value() == "manual":
            return queryset.filter(source_pair__isnull=True)
        return queryset


@admin.register(MatchAlternative)
class MatchAlternativeAdmin(admin.ModelAdmin):
    form = MatchAlternativeForm
    list_display = ("match", "alternative", "effective_week", "source", "source_pair_display")
    list_filter = (AlternativeWeekFilter, AlternativeSourceFilter, "match__round")
    list_select_related = ("match__round", "alternative", "source_pair__match_a", "source_pair__match_b")
    search_fields = ("match__home_team", "match__away_team", "alternative__home_team", "alternative__away_team")
    ordering = ("-match__round__ranking_date", "match__kickoff", "match_id")

    def get_readonly_fields(self, request, obj=None):
        return ("match", "alternative", "source_pair") if obj else ("source_pair",)

    @admin.display(description="KW")
    def effective_week(self, obj):
        return str(obj.match.effective_kw)

    @admin.display(description="Source")
    def source(self, obj):
        return "DRAFT" if obj.source_pair_id else "MANUAL"

    @admin.display(description="Source pair")
    def source_pair_display(self, obj):
        if not obj.source_pair_id:
            return "-"
        return f"#{obj.source_pair_id}: {obj.source_pair.match_a} / {obj.source_pair.match_b}"

    def has_delete_permission(self, request, obj=None):
        # Relationships are durable once exposed to players.
        return False
admin.site.register(Match50Season)
admin.site.register(Prediction)
admin.site.register(Team)
admin.site.register(Competition)
admin.site.register(CompetitionSeason)
admin.site.register(GlobalModifier)
admin.site.register(DraftModifierVote)
