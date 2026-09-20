from django import template
from matches.services.league_flags import league_label

register = template.Library()
register.filter("league_label", league_label)
