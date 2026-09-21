from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("typy/", views.typy, name="typy"),
    path("obstaw/", views.typy, name="obstaw"),
    path("draft/", views.draft, name="draft"),
    path("draft/pairs/<int:pair_id>/vote/", views.draft_vote, name="draft_vote"),
    path("draft/modifiers/<int:modifier_id>/vote/", views.draft_modifier_vote, name="draft_modifier_vote"),
    path("rankings/", views.rankings, name="rankings"),
    path("players/<str:username>/history/", views.player_round_history, name="player_round_history"),
    path("players/<str:username>/", views.player_profile, name="player_profile"),
    path("stats/", views.placeholder, {"section": "stats"}, name="stats"),
    path("rules/", views.placeholder, {"section": "rules"}, name="rules"),
    path("login/", LoginView.as_view(template_name="matches/login.html"), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
]
