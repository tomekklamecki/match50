from django import template
from django.utils.html import format_html

from matches.services.team_visuals import shirt_config, team_visual_config

register = template.Library()


@register.simple_tag
def team_shirt(team, label="Drużyna"):
    config = shirt_config(team)
    return format_html(
        '<svg class="team-shirt" data-team-shirt role="img" aria-label="Koszulka: {}" '
        'viewBox="0 0 24 26" style="--shirt-primary:{};--shirt-secondary:{}" data-shirt-pattern="{}">'
        '<path class="shirt-base" d="M7 3 10 1h4l3 2 5 4-3 5-2-1v14H7V11l-2 1-3-5 5-4Z"/>'
        '<path class="shirt-body" d="M7 3 10 1h4l3 2v22H7V3Z"/>'
        '<path class="shirt-half" d="M12 1h2l3 2 5 4-3 5-2-1v14h-5V1Z"/>'
        '<g class="shirt-stripes"><path d="M9 3h2v22H9zM13 2h2v23h-2z"/></g>'
        '<g class="shirt-hoops"><path d="M7 7h10v3H7zM7 14h10v3H7zM7 21h10v3H7z"/></g>'
        '<path class="shirt-outline" d="M7 3 10 1h4l3 2 5 4-3 5-2-1v14H7V11l-2 1-3-5 5-4Z"/>'
        '<path class="shirt-neck" d="M10 1c.2 1.3.9 2 2 2s1.8-.7 2-2"/>'
        '</svg>', label, config["primary"], config["secondary"], config["pattern"],
    )


@register.simple_tag
def team_visual(team, label="Team"):
    """Render the canonical team visual without exposing unresolved country codes."""
    flag = team_visual_config(team)["flag"]
    if not flag:
        return team_shirt(team, label)
    if "asset" in flag:
        return format_html(
            '<img class="draft-team-visual national-team-flag flag-asset" src="{}" '
            'alt="" role="img" aria-label="{}">',
            flag["asset"], label,
        )
    return format_html(
        '<span class="draft-team-visual national-team-flag" role="img" aria-label="{}">{}</span>',
        label, flag["text"],
    )
