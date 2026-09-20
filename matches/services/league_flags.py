"""Football-country identity for prediction labels; stored league names stay intact."""
from django.utils.html import format_html


LEAGUE_FLAGS = {
    "Premier League": ("England", '<path fill="#fff" d="M0 0h24v16H0z"/><path fill="#ce1124" d="M10 0h4v16h-4zM0 6h24v4H0z"/>'),
    "La Liga": ("Spain", '<path fill="#aa151b" d="M0 0h24v16H0z"/><path fill="#f1bf00" d="M0 4h24v8H0z"/>'),
    "Serie A": ("Italy", '<path fill="#009246" d="M0 0h8v16H0z"/><path fill="#fff" d="M8 0h8v16H8z"/><path fill="#ce2b37" d="M16 0h8v16h-8z"/>'),
    "Bundesliga": ("Germany", '<path fill="#000" d="M0 0h24v16H0z"/><path fill="#d00" d="M0 5.33h24v10.67H0z"/><path fill="#ffce00" d="M0 10.67h24V16H0z"/>'),
    "Ligue 1": ("France", '<path fill="#002395" d="M0 0h8v16H0z"/><path fill="#fff" d="M8 0h8v16H8z"/><path fill="#ed2939" d="M16 0h8v16h-8z"/>'),
    "Ekstraklasa": ("Poland", '<path fill="#fff" d="M0 0h24v16H0z"/><path fill="#dc143c" d="M0 8h24v8H0z"/>'),
}


def flag_markup(league):
    from django.utils.safestring import mark_safe
    identity = LEAGUE_FLAGS.get(league)
    if not identity:
        return ""
    country, paths = identity
    # Only the fixed SVG paths above are trusted; league names are never markup.
    return format_html('<svg class="league-flag" role="img" aria-label="{}" viewBox="0 0 24 16" width="16" height="11" style="vertical-align:-1px;flex-shrink:0;border:1px solid #d9dfe8">{}</svg>', country, mark_safe(paths))


def flag_presentations():
    return {league: {"country": identity[0], "svg": str(flag_markup(league))} for league, identity in LEAGUE_FLAGS.items()}


def league_label(league):
    flag = flag_markup(league)
    return format_html('{} {}', flag, league) if flag else format_html('{}', league)
