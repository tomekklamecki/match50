from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("typy/", views.typy, name="typy"),
    path("obstaw/", views.typy, name="obstaw"),
    path("draft/", views.draft, name="draft"),
    path("draft/pairs/<int:pair_id>/vote/", views.draft_vote, name="draft_vote"),
    path("rankings/", views.placeholder, {"section": "rankings"}, name="rankings"),
    path("stats/", views.placeholder, {"section": "stats"}, name="stats"),
    path("rules/", views.placeholder, {"section": "rules"}, name="rules"),
    path("login/", LoginView.as_view(template_name="matches/login.html"), name="login"),
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
]
