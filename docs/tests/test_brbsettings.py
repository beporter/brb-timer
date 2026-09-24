import json
import unittest
from unittest.mock import patch, Mock

from brb_timer import BRBSettings


class TestBRBSettings(unittest.TestCase):

    def setUp(self):
        self.settings = BRBSettings()

    def tearDown(self):
        pass

    def test_dataclass(self):
        # Verify default values.
        self.assertEqual(self.settings.twitch_oauth_access_token, '')
        self.assertEqual(self.settings.twitch_oauth_expiry_at, 0)
        self.assertEqual(self.settings.twitch_broadcaster_id, 0)
        self.assertEqual(self.settings.twitch_username, '')
        self.assertEqual(self.settings.twitch_channel, '')
        self.assertEqual(
            self.settings.auto_hide_secs,
            BRBSettings().auto_hide_secs,
        )

        # Existing attributes can be changed.
        self.settings.twitch_username = 'test_user'
        self.assertEqual(self.settings.twitch_username, 'test_user')

        # Arbitrary attributes cannot be added.
        with self.assertRaises(AttributeError):
            self.settings.some_new_attr = 'value'

    @patch('brb_timer.OBS.data_get_all')
    def test_from_data(self, mock_data_get_all):
        mock_data_get_all.return_value = {
            'twitch_oauth_access_token': 'oauth-token',
            'twitch_oauth_expiry_at': 1234567890,
            'twitch_broadcaster_id': 12345,
            'twitch_username': 'test_user',
            'twitch_channel': 'test_channel',
            'auto_hide_secs': 30,

            'some_new_setting': 'should be ignored',
        }

        data = Mock()
        self.settings.from_data(data)

        mock_data_get_all.assert_called_once_with(data)
        self.assertEqual(
            self.settings.twitch_oauth_access_token,
            'oauth-token',
        )
        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            1234567890,
        )
        self.assertEqual(
            self.settings.twitch_broadcaster_id,
            12345,
        )
        self.assertEqual(
            self.settings.twitch_username,
            'test_user',
        )
        self.assertEqual(
            self.settings.twitch_channel,
            'test_channel',
        )
        self.assertEqual(self.settings.auto_hide_secs, 30)

        self.assertFalse(
            hasattr(self.settings, 'some_new_setting'),
            'Unknown incoming attributes must not be added.',
        )

    @patch('brb_timer.OBS.data_set')
    def test_to_data(self, mock_data_set):
        self.settings.twitch_oauth_access_token = 'oauth-token'
        self.settings.twitch_oauth_expiry_at = 1234567890
        self.settings.twitch_broadcaster_id = 12345
        self.settings.twitch_username = 'test_user'
        self.settings.twitch_channel = 'test_channel'
        self.settings.auto_hide_secs = 30

        existing = Mock()
        self.settings.to_data(existing)

        expected_calls = [
            unittest.mock.call(
                existing,
                'twitch_oauth_access_token',
                'oauth-token',
            ),
            unittest.mock.call(
                existing,
                'twitch_oauth_expiry_at',
                1234567890,
            ),
            unittest.mock.call(
                existing,
                'twitch_broadcaster_id',
                12345,
            ),
            unittest.mock.call(
                existing,
                'twitch_username',
                'test_user',
            ),
            unittest.mock.call(
                existing,
                'twitch_channel',
                'test_channel',
            ),
            unittest.mock.call(
                existing,
                'auto_hide_secs',
                30,
            ),
        ]

        self.assertEqual(mock_data_set.call_args_list, expected_calls)

    def test_to_json(self):
        expected = {
            'twitch_oauth_access_token': 'oauth-token',
            'twitch_oauth_expiry_at': 1234567890,
            'twitch_broadcaster_id': 12345,
            'twitch_username': 'test_user',
            'twitch_channel': 'test_channel',
            'auto_hide_secs': 30,
        }
        self.settings.twitch_oauth_access_token = 'oauth-token'
        self.settings.twitch_oauth_expiry_at = 1234567890
        self.settings.twitch_broadcaster_id = 12345
        self.settings.twitch_username = 'test_user'
        self.settings.twitch_channel = 'test_channel'
        self.settings.auto_hide_secs = 30

        result = self.settings.to_json()
        data = json.loads(result)

        self.assertEqual(data, expected)


if __name__ == '__main__':
    unittest.main()
