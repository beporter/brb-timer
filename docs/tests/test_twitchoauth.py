import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brb_timer import TwitchOAuth

class TestTwitchOAuth(unittest.TestCase):

    def setUp(self):
        self.settings = SimpleNamespace(
            twitch_oauth_access_token="",
            twitch_oauth_expiry_at=0,
            twitch_broadcaster_id=-1,
            twitch_username="",
            twitch_channel="",
        )

        self.oauth = TwitchOAuth(self.settings)

    def tearDown(self):
        pass

    def test_kickoff_url(self):
        self.assertEqual(
            self.oauth.kickoff_url(),
            TwitchOAuth.KICKOFF_URL,
        )

    # ------------------------------------------------------------------
    # creds_present()

    def test_creds_present(self):
        test_cases = (
            ("None token", None, 123, False),
            ("empty token", "", 123, False),
            ("None expiry", "token", None, False),
            ("zero expiry", "token", 0, False),
        )

        for name, token, expiry, expected in test_cases:
            with self.subTest(name=name):
                self.settings.twitch_oauth_access_token = token
                self.settings.twitch_oauth_expiry_at = expiry

                with patch.object(
                    self.oauth,
                    "creds_expired",
                    return_value=False,
                ) as expired:
                    result = self.oauth.creds_present()

                self.assertEqual(result, expected)

                if expected:
                    expired.assert_called_once()
                else:
                    expired.assert_not_called()

    def test_creds_present_valid_credentials(self):
        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = 1234567890

        with patch.object(
            self.oauth,
            "creds_expired",
            return_value=False,
        ) as expired:
            result = self.oauth.creds_present()

        self.assertTrue(result)
        expired.assert_called_once()

    def test_creds_present_expired_credentials(self):
        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = 1234567890

        with patch.object(
            self.oauth,
            "creds_expired",
            return_value=True,
        ) as expired:
            result = self.oauth.creds_present()

        self.assertFalse(result)
        expired.assert_called_once()

    # ------------------------------------------------------------------
    # creds_expired()

    def test_creds_expired(self):
        """
        Exercise datetime, ISO-string, and epoch expiry representations.
        """
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)

        cases = (
            (
                "datetime expired",
                now - datetime.timedelta(seconds=1),
                True,
            ),
            (
                "datetime valid",
                now + datetime.timedelta(seconds=1),
                False,
            ),
        )

        for name, expiry, expected in cases:
            with self.subTest(name=name):
                self.settings.twitch_oauth_access_token = "token"
                self.settings.twitch_oauth_expiry_at = expiry

                with patch("brb_timer.DT.now", return_value=now):
                    result = self.oauth.creds_expired()

                self.assertEqual(result, expected)

                # Datetime values are normalized to epoch seconds.
                self.assertIsInstance(
                    self.settings.twitch_oauth_expiry_at,
                    int,
                )

    def test_creds_expired_iso_string(self):
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        expiry = now + datetime.timedelta(hours=1)

        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = "2025-01-01T13:00:00"

        with patch(
            "brb_timer.DT.iso_str_to_dt",
            return_value=expiry,
        ) as iso_to_dt:
            with patch(
                "brb_timer.DT.now",
                return_value=now,
            ):
                result = self.oauth.creds_expired()

        self.assertFalse(result)

        iso_to_dt.assert_called_once_with(
            "2025-01-01T13:00:00",
        )

        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            int(expiry.timestamp()),
        )

    def test_creds_expired_epoch(self):
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        expiry = now + datetime.timedelta(hours=1)
        expiry_epoch = int(expiry.timestamp())

        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = expiry_epoch

        with patch(
            "brb_timer.DT.epoch_secs_to_dt",
            return_value=expiry,
        ) as epoch_to_dt:
            with patch(
                "brb_timer.DT.now",
                return_value=now,
            ):
                result = self.oauth.creds_expired()

        self.assertFalse(result)

        epoch_to_dt.assert_called_once_with(
            expiry_epoch,
        )

    def test_creds_expired_requires_token_and_expiry(self):
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        expired = now - datetime.timedelta(seconds=1)

        cases = (
            ("no token", None, expired),
            ("empty token", "", expired),
            ("no expiry", "token", None),
            ("zero expiry", "token", 0),
        )

        for name, token, expiry in cases:
            with self.subTest(name=name):
                self.settings.twitch_oauth_access_token = token
                self.settings.twitch_oauth_expiry_at = expiry

                with patch("brb_timer.DT.now", return_value=now):
                    result = self.oauth.creds_expired()

                self.assertFalse(result)

    # ------------------------------------------------------------------
    # creds_set()

    def test_creds_set_valid_token(self):
        self.settings.twitch_oauth_access_token = "old-token"
        self.settings.twitch_oauth_expiry_at = 100

        with patch.object(
            self.oauth,
            "user_get",
            return_value={"id": 123, "name": "streamer"},
        ) as user_get, patch.object(
            self.oauth,
            "channel_get",
            return_value="streamer",
        ) as channel_get:

            result = self.oauth.creds_set(
                "  new-token  ",
                1234567890,
            )

        self.assertTrue(result)

        self.assertEqual(
            self.settings.twitch_oauth_access_token,
            "new-token",
        )
        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            1234567890,
        )

        user_get.assert_called_once_with()
        channel_get.assert_called_once_with()

    def test_creds_set_invalid_token(self):
        self.settings.twitch_oauth_access_token = "old-token"
        self.settings.twitch_oauth_expiry_at = 100
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_username = "old-user"
        self.settings.twitch_channel = "old-channel"

        with patch.object(
            self.oauth,
            "user_get",
            return_value=False,
        ) as user_get:

            result = self.oauth.creds_set(
                "bad-token",
                1234567890,
            )

        self.assertFalse(result)

        user_get.assert_called_once_with()

        # Invalid token causes settings to be cleared.
        self.assertEqual(
            self.settings.twitch_oauth_access_token,
            "",
        )
        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            0,
        )
        self.assertEqual(
            self.settings.twitch_broadcaster_id,
            -1,
        )
        self.assertEqual(
            self.settings.twitch_username,
            "",
        )
        self.assertEqual(
            self.settings.twitch_channel,
            "",
        )

    def test_creds_set_datetime_expiry(self):
        expiry = datetime.datetime(2025, 1, 1, 12, 0, 0)

        with patch.object(
            self.oauth,
            "user_get",
            return_value={"id": 123, "name": "streamer"},
        ), patch.object(
            self.oauth,
            "channel_get",
            return_value="streamer",
        ):
            result = self.oauth.creds_set(
                "token",
                expiry,
            )

        self.assertTrue(result)
        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            int(expiry.timestamp()),
        )

    def test_creds_set_uses_existing_iso_expiry(self):
        # Regression test. For a period of development, the
        # twitch_oauth_expiry_at was getting saved to OBS's internal
        # json storage as an ISO string. This "corrects" any iso
        # strings encountered in the wild.
        self.settings.twitch_oauth_expiry_at = "2025-01-01T12:00:00"
        converted = datetime.datetime(
            2025,
            1,
            1,
            12,
            0,
            0,
        )

        with patch(
            "brb_timer.DT.iso_str_to_dt",
            return_value=converted,
        ) as iso_to_dt, patch.object(
            self.oauth,
            "clear_settings", # Don't wipe out the setting we've overridden.
        ), patch.object(
            self.oauth,
            "user_get",
            return_value={"id": 123, "name": "streamer"},
        ), patch.object(
            self.oauth,
            "channel_get",
            return_value="streamer",
        ):
            result = self.oauth.creds_set(
                "token",
                9999999999,
            )

        self.assertTrue(result)

        iso_to_dt.assert_called_once_with(
            "2025-01-01T12:00:00",
        )

        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            int(converted.timestamp()),
        )

    # ------------------------------------------------------------------
    # user_present()

    def test_user_present(self):
        cases = (
            ("missing ID", -1, "streamer", False),
            ("zero ID", 0, "streamer", False),
            ("valid ID missing name", 123, "", False),
            ("valid ID and name", 123, "streamer", True),
        )

        for name, broadcaster_id, username, expected in cases:
            with self.subTest(name=name):
                self.settings.twitch_broadcaster_id = broadcaster_id
                self.settings.twitch_username = username

                self.assertEqual(
                    self.oauth.user_present(),
                    expected,
                )

    # ------------------------------------------------------------------
    # user_get()

    def test_user_get_when_user_already_present(self):
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_username = "streamer"

        with patch.object(
            self.oauth,
            "_api",
        ) as api:
            result = self.oauth.user_get()

        self.assertEqual(
            result,
            {
                "id": 123,
                "name": "streamer",
            },
        )

        api.assert_not_called()

    def test_user_get_validates_credentials(self):
        self.settings.twitch_broadcaster_id = -1
        self.settings.twitch_username = ""

        api = MagicMock()
        validated = {
            "expires_at": datetime.datetime(
                2025,
                1,
                1,
                13,
                0,
                0,
            ),
            "broadcaster_id": 123,
            "login": "streamer",
        }
        api.validate.return_value = validated

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "_api",
            return_value=api,
        ):

            result = self.oauth.user_get()

        self.assertEqual(
            result,
            {
                "id": 123,
                "name": "streamer",
            },
        )

        api.validate.assert_called_once_with()

        self.assertEqual(
            self.settings.twitch_broadcaster_id,
            123,
        )
        self.assertEqual(
            self.settings.twitch_username,
            "streamer",
        )
        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            int(validated["expires_at"].timestamp()),
        )

    def test_user_get_without_credentials(self):
        self.settings.twitch_broadcaster_id = -1
        self.settings.twitch_username = ""

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=False,
        ), patch.object(
            self.oauth,
            "_api",
        ) as api:

            result = self.oauth.user_get()

        self.assertEqual(
            result,
            {
                "id": -1,
                "name": "",
            },
        )

        api.assert_not_called()

    def test_user_get_validation_failure(self):
        self.settings.twitch_broadcaster_id = -1
        self.settings.twitch_username = ""

        api = MagicMock()
        api.validate.return_value = False

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "_api",
            return_value=api,
        ):

            result = self.oauth.user_get()

        self.assertFalse(result)
        api.validate.assert_called_once_with()

    # ------------------------------------------------------------------
    # user_set()

    def test_user_set(self):
        result = self.oauth.user_set(
            123,
            "streamer",
        )

        self.assertTrue(result)
        self.assertEqual(
            self.settings.twitch_broadcaster_id,
            123,
        )
        self.assertEqual(
            self.settings.twitch_username,
            "streamer",
        )

    # ------------------------------------------------------------------
    # channel_present()

    def test_channel_present(self):
        cases = (
            ("missing ID", -1, "streamer", False),
            ("zero ID", 0, "streamer", False),
            ("missing channel", 123, "", False),
            ("valid channel", 123, "streamer", True),
        )

        for name, broadcaster_id, channel, expected in cases:
            with self.subTest(name=name):
                self.settings.twitch_broadcaster_id = broadcaster_id
                self.settings.twitch_channel = channel

                self.assertEqual(
                    self.oauth.channel_present(),
                    expected,
                )

    # ------------------------------------------------------------------
    # channel_get()

    def test_channel_get_when_channel_already_present(self):
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_channel = "streamer"

        with patch.object(
            self.oauth,
            "_api",
        ) as api:
            result = self.oauth.channel_get()

        self.assertEqual(result, "streamer")
        api.assert_not_called()

    def test_channel_get_fetches_channel(self):
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_channel = ""

        api = MagicMock()
        api.channel.return_value = {
            "broadcaster_name": "streamer",
        }

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "user_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "_api",
            return_value=api,
        ):

            result = self.oauth.channel_get()

        self.assertEqual(
            result,
            "streamer",
        )

        api.channel.assert_called_once_with(123)
        self.assertEqual(
            self.settings.twitch_channel,
            "streamer",
        )

    def test_channel_get_without_credentials(self):
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_channel = ""

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=False,
        ), patch.object(
            self.oauth,
            "_api",
        ) as api:

            result = self.oauth.channel_get()

        self.assertEqual(result, "")
        api.assert_not_called()

    def test_channel_get_without_user(self):
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_channel = ""

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "user_present",
            return_value=False,
        ), patch.object(
            self.oauth,
            "_api",
        ) as api:

            result = self.oauth.channel_get()

        self.assertEqual(result, "")
        api.assert_not_called()

    def test_channel_get_api_failure(self):
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_channel = ""

        api = MagicMock()
        api.channel.return_value = False

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "user_present",
            return_value=True,
        ), patch.object(
            self.oauth,
            "_api",
            return_value=api,
        ):

            result = self.oauth.channel_get()

        self.assertEqual(result, "")
        api.channel.assert_called_once_with(123)

    # ------------------------------------------------------------------
    # channel_set()

    def test_channel_set(self):
        result = self.oauth.channel_set("streamer")

        self.assertTrue(result)
        self.assertEqual(
            self.settings.twitch_channel,
            "streamer",
        )

    # ------------------------------------------------------------------
    # clear_settings()

    def test_clear_settings(self):
        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = 123456
        self.settings.twitch_broadcaster_id = 123
        self.settings.twitch_username = "streamer"
        self.settings.twitch_channel = "channel"

        self.oauth.api = MagicMock()

        result = self.oauth.clear_settings()

        self.assertIsNone(result)

        self.assertEqual(
            self.settings.twitch_oauth_access_token,
            "",
        )
        self.assertEqual(
            self.settings.twitch_oauth_expiry_at,
            0,
        )
        self.assertEqual(
            self.settings.twitch_broadcaster_id,
            -1,
        )
        self.assertEqual(
            self.settings.twitch_username,
            "",
        )
        self.assertEqual(
            self.settings.twitch_channel,
            "",
        )

        self.assertIsNone(self.oauth.api)

    # ------------------------------------------------------------------
    # _api()

    def test__api_without_credentials(self):
        self.oauth.api = None

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=False,
        ):
            result = self.oauth._api()

        self.assertFalse(result)
        self.assertIsNone(self.oauth.api)

    def test__api_creates_api(self):
        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = 123456

        self.oauth.api = None

        fake_api = MagicMock()

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch(
            "brb_timer.TwitchApi",
            return_value=fake_api,
        ) as TwitchApi:

            result = self.oauth._api()

        self.assertIs(result, fake_api)
        self.assertIs(self.oauth.api, fake_api)

        TwitchApi.assert_called_once_with(
            TwitchOAuth.APP_CLIENT_ID,
            "token",
        )

    def test__api_reuses_existing_api(self):
        self.settings.twitch_oauth_access_token = "token"
        self.settings.twitch_oauth_expiry_at = 123456

        existing_api = MagicMock()
        self.oauth.api = existing_api

        with patch.object(
            self.oauth,
            "creds_present",
            return_value=True,
        ), patch(
            "brb_timer.TwitchApi",
        ) as TwitchApi:

            result = self.oauth._api()

        self.assertIs(result, existing_api)
        TwitchApi.assert_not_called()

if __name__ == '__main__':
    unittest.main()
