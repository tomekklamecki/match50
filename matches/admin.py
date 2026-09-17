from django.contrib import admin
from .models import Match, Round, Prediction


admin.site.register(Match)
admin.site.register(Round)
admin.site.register(Prediction)