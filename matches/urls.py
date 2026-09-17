from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("obstaw/", views.obstaw, name="obstaw"),

    path(
        "login/",
        LoginView.as_view(template_name="matches/login.html"),
        name="login"
    ),

    path(
        "logout/",
        LogoutView.as_view(next_page="/"),
        name="logout"
    ),
]