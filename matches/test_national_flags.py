from django.test import SimpleTestCase
from types import SimpleNamespace

from .services.league_flags import FOOTBALL_FLAG_OVERRIDES, ISO_ALPHA_2, iso_country_flag, national_team_flag
from .services.team_visuals import match_presentation, team_visual_config


class NationalFlagTests(SimpleTestCase):
    def test_iso_conversion_and_senior_teams_share_one_path(self):
        cases = [(768, 'IT', '\U0001f1ee\U0001f1f9'), (1, 'BE', '\U0001f1e7\U0001f1ea'),
                 (25, 'DE', '\U0001f1e9\U0001f1ea'), (1117, 'GR', '\U0001f1ec\U0001f1f7'),
                 (1118, 'NL', '\U0001f1f3\U0001f1f1'), (775, 'AT', '\U0001f1e6\U0001f1f9'),
                 (9, 'ES', '\U0001f1ea\U0001f1f8'), (2, 'FR', '\U0001f1eb\U0001f1f7'),
                 (24, 'PL', '\U0001f1f5\U0001f1f1')]
        for provider_id, code, emoji in cases:
            with self.subTest(code=code):
                self.assertEqual(iso_country_flag(code.lower()), emoji)
                self.assertEqual(national_team_flag(SimpleNamespace(api_football_id=provider_id)),
                                 {'country': code, 'text': emoji})
        self.assertEqual(len(ISO_ALPHA_2), 249)

    def test_invalid_or_missing_code_never_returns_text(self):
        for code in (None, '', 'ZZ', 'UK', 'XK', 'GB-ENG', 'ITA', '1T', 42):
            with self.subTest(code=code):
                self.assertIsNone(iso_country_flag(code))
        self.assertIsNone(national_team_flag(SimpleNamespace(api_football_id=999999)))

    def test_football_association_overrides_use_local_assets_not_iso_codes(self):
        expected = {
            10: ('England', 'GB-ENG', 'england.svg'),
            1108: ('Scotland', 'GB-SCT', 'scotland.svg'),
            767: ('Wales', 'GB-WLS', 'wales.svg'),
            771: ('Northern Ireland', 'GB-NIR', 'northern-ireland.svg'),
            1111: ('Kosovo', 'XK', 'kosovo.svg'),
        }
        self.assertEqual(set(FOOTBALL_FLAG_OVERRIDES), set(expected))
        for provider_id, (name, code, asset_name) in expected.items():
            with self.subTest(name=name):
                flag = national_team_flag(SimpleNamespace(api_football_id=provider_id))
                self.assertEqual((flag['name'], flag['country']), (name, code))
                self.assertTrue(flag['asset'].endswith(asset_name))
                self.assertNotIn('text', flag)

    def test_identity_not_name_or_competition_selects_flag(self):
        self.assertEqual(national_team_flag(SimpleNamespace(api_football_id=25))['country'], 'DE')
        self.assertEqual(national_team_flag(SimpleNamespace(api_football_id=1117))['text'], '🇬🇷')
        for provider_id in (None, 42, 999999):
            self.assertIsNone(national_team_flag(SimpleNamespace(api_football_id=provider_id, name='Germany')))
        self.assertIsNone(national_team_flag(None))

    def test_club_keeps_shirt_and_effective_fixture_selects_own_flags(self):
        def team(pk):
            return SimpleNamespace(api_football_id=pk, shirt_primary='#112233', shirt_secondary='#ffffff', shirt_pattern='SOLID')
        fixture = SimpleNamespace(home_team='Club', away_team='Other', home_team_entity=team(42), away_team_entity=None)
        club = match_presentation(fixture)
        self.assertIsNone(club['homeFlag'])
        self.assertEqual(club['homeShirt']['primary'], '#112233')
        fixture.home_team_entity = team(1118)
        self.assertEqual(match_presentation(fixture)['homeFlag']['country'], 'NL')

    def test_shared_visual_config_is_flag_then_shirt_fallback(self):
        national = team_visual_config(SimpleNamespace(api_football_id=10, shirt_primary='#112233', shirt_secondary='#ffffff', shirt_pattern='SOLID'))
        self.assertTrue(national['flag']['asset'].endswith('england.svg'))
        self.assertEqual(national['shirt']['primary'], '#112233')
        unknown = team_visual_config(SimpleNamespace(api_football_id=999999, shirt_primary='#112233', shirt_secondary='#ffffff', shirt_pattern='SOLID'))
        self.assertIsNone(unknown['flag'])
        self.assertEqual(unknown['shirt']['primary'], '#112233')
