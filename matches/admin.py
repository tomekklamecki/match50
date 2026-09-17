from django import forms
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, reverse

from .models import Draft, DraftPair, DraftVote, Match, Prediction, Round


@admin.register(Draft)
class DraftAdmin(admin.ModelAdmin):
    list_display = ("name", "starts_at", "is_active", "pair_count", "next_round")
    list_filter = ("is_active",)

    @admin.display(description="Pairs")
    def pair_count(self, obj):
        return obj.pairs.count()


@admin.register(DraftPair)
class DraftPairAdmin(admin.ModelAdmin):
    list_display = ("draft", "day_number", "match_a", "match_b", "winner", "resolution_method", "resolved_at")
    list_filter = ("draft", "day_number", "resolution_method")
    readonly_fields = ("winner", "resolution_method", "resolved_at")

    class PairForm(forms.ModelForm):
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
            draft_id = self.data.get("draft") or self.initial.get("draft") or self.instance.draft_id
            available = Match.objects.exclude(round__is_active=True)
            if draft_id:
                used_ids = DraftPair.objects.filter(draft_id=draft_id).exclude(pk=self.instance.pk).values_list("match_a_id", "match_b_id")
                unavailable_ids = {match_id for pair_ids in used_ids for match_id in pair_ids}
                available = available.exclude(pk__in=unavailable_ids)
            self.fields["match_a"].queryset = available.order_by("league", "kickoff", "home_team")
            self.fields["match_b"].queryset = available.order_by("league", "kickoff", "home_team")
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
            return JsonResponse({"candidates": []})
        pairs = DraftPair.objects.filter(draft_id=draft_id)
        if current_pair_id:
            pairs = pairs.exclude(pk=current_pair_id)
        used_ids = {match_id for match_ids in pairs.values_list("match_a_id", "match_b_id") for match_id in match_ids}
        candidates = Match.objects.exclude(round__is_active=True).exclude(pk__in=used_ids).order_by("league", "kickoff", "home_team")
        pair_count = DraftPair.objects.filter(draft_id=draft_id).count()
        return JsonResponse({"next_day": min(pair_count // 5 + 1, 6), "candidates": [
            {"id": match.id, "text": f"{match.league} — {match.home_team} - {match.away_team} ({match.kickoff:%d.%m %H:%M})"}
            for match in candidates
        ]})

    class Media:
        js = ("matches/draft_pair_admin.js",)


@admin.register(DraftVote)
class DraftVoteAdmin(admin.ModelAdmin):
    list_display = ("pair", "user", "selected_match", "created_at")
    readonly_fields = ("pair", "user", "selected_match", "created_at")


admin.site.register(Match)
admin.site.register(Round)
admin.site.register(Prediction)
