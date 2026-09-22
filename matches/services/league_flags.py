"""Football-country identity for prediction labels; stored league names stay intact."""
from django.utils.html import format_html
from django.templatetags.static import static


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


# Explicit senior identities from the provider-backed UEFA Nations League pool.
# IDs identify teams, not their names or the country of a competition. Youth,
# women's and club identities are not implicitly included. Extend only with
# verified senior provider identities; unknown teams retain their shirt.
SENIOR_NATIONAL_COUNTRIES = {
    778: 'AL', 1110: 'AD', 1094: 'AM', 775: 'AT', 1096: 'AZ',
    1100: 'BY', 1: 'BE', 1113: 'BA', 1103: 'BG', 3: 'HR',
    1106: 'CY', 770: 'CZ', 21: 'DK', 10: 'GB-ENG', 1101: 'EE',
    1105: 'MK', 1098: 'FO', 1099: 'FI', 2: 'FR', 1104: 'GE',
    25: 'DE', 1093: 'GI', 1117: 'GR', 769: 'HU', 18: 'IS',
    1116: 'IL', 768: 'IT', 1095: 'KZ', 1111: 'XK', 1092: 'LV',
    1107: 'LI', 1097: 'LT', 1102: 'LU', 1112: 'MT', 1114: 'MD',
    1109: 'ME', 1118: 'NL', 1090: 'NO', 24: 'PL', 27: 'PT',
    776: 'IE', 774: 'RO', 1115: 'SM', 14: 'RS', 773: 'SK',
    1091: 'SI', 9: 'ES', 5: 'SE', 15: 'CH', 777: 'TR', 772: 'UA',
}


# These are senior football associations whose provider identities do not have
# assigned ISO 3166-1 alpha-2 country codes.  They intentionally use local
# flag assets rather than weakening ISO validation below.
FOOTBALL_FLAG_OVERRIDES = {
    10: ('England', 'GB-ENG', 'matches/football-flags/england.svg'),
    1108: ('Scotland', 'GB-SCT', 'matches/football-flags/scotland.svg'),
    767: ('Wales', 'GB-WLS', 'matches/football-flags/wales.svg'),
    771: ('Northern Ireland', 'GB-NIR', 'matches/football-flags/northern-ireland.svg'),
    1111: ('Kosovo', 'XK', 'matches/football-flags/kosovo.svg'),
}


ISO_ALPHA_2 = frozenset('''
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT
JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW
SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
'''.split())


def iso_country_flag(code):
    """Return an emoji only for an assigned ISO alpha-2 code; never raw text."""
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    if code not in ISO_ALPHA_2:
        return None
    return ''.join(chr(0x1F1E6 + ord(letter) - ord('A')) for letter in code)


def national_team_flag(team):
    provider_id = team.api_football_id if team else None
    override = FOOTBALL_FLAG_OVERRIDES.get(provider_id)
    if override:
        name, country, asset = override
        return {'country': country, 'asset': static(asset), 'name': name}
    country = SENIOR_NATIONAL_COUNTRIES.get(provider_id)
    emoji = iso_country_flag(country)
    return {'country': country, 'text': emoji} if emoji else None
