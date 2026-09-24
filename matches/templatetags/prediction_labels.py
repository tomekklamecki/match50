from django import template
from matches.services.league_flags import league_label
from matches.services.prediction_ui import chip_display_label
from matches.services.calendar_week import round_week_label

register = template.Library()
register.filter("league_label", league_label)
register.filter("chip_label", chip_display_label)
register.simple_tag(round_week_label)
