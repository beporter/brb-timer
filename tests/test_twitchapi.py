import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from brb_timer import TwitchApi


class TestTwitchApi(unittest.TestCase):

    def setUp(self):
        self.client_id = 'test-client-id'
        self.token = 'test-token'
        self.api = TwitchApi(self.client_id, self.token)

    def tearDown(self):
        pass

    def test_validate(self):
        response = {
            'client_id': self.client_id,
            'login': 'test_user',
            'scopes': [
                'scope:one',
                'scope:two',
            ],
            'user_id': '12345',
            'expires_in': 3600,
        }

        with patch('brb_timer.TwitchOAuth.OAUTH_SCOPES', [
            'scope:one',
            'scope:two',
        ]), \
        patch.object(self.api, '_get', return_value=response) as mock_get, \
        patch('brb_timer.DT.duration_secs_to_dt') as mock_duration:

            expected_expiry = Mock()
            mock_duration.return_value = expected_expiry

            result = self.api.validate()

        mock_get.assert_called_once_with('oauth2/validate')
        mock_duration.assert_called_once_with(3600)

        self.assertEqual(result, {
            'client_id': self.client_id,
            'login': 'test_user',
            'scopes': [
                'scope:one',
                'scope:two',
            ],
            'broadcaster_id': 12345,
            'expires_in': 3600,
            'expires_at': expected_expiry,
        })

    def test_validate_get_failure(self):
        with patch.object(self.api, '_get', return_value=False), \
             patch('brb_timer.OBS.error') as mock_error:

            result = self.api.validate()

        self.assertFalse(result)
        mock_error.assert_called_once_with(
            'oauth2/validate failed.'
        )

    def test_validate_no_login(self):
        response = {
            'client_id': self.client_id,
            'login': '',
            'scopes': [],
            'user_id': '12345',
        }

        with patch.object(self.api, '_get', return_value=response), \
             patch('brb_timer.OBS.error') as mock_error:

            result = self.api.validate()

        self.assertFalse(result)
        mock_error.assert_called_once_with(
            'OAuth token is not attached to a user.'
        )

    def test_validate_missing_scopes(self):
        response = {
            'login': 'test_user',
            'scopes': ['scope:one'],
        }

        with patch(
            'brb_timer.TwitchOAuth.OAUTH_SCOPES',
            ['scope:one', 'scope:two'],
        ), \
        patch.object(self.api, '_get', return_value=response), \
        patch('brb_timer.OBS.error') as mock_error:

            result = self.api.validate()

        self.assertFalse(result)

        mock_error.assert_called_once()

        error_message = mock_error.call_args.args[0]
        self.assertIn('OAuth token is lacking necessary scopes:', error_message)
        self.assertIn('scope:two', error_message)

    def test_channel(self):
        response = {
            'data': [{
                'broadcaster_id': '12345',
                'broadcaster_login': 'test_user',
                'broadcaster_name': 'Test User',
                'broadcaster_language': 'en',
                'game_id': '67890',
                'game_name': 'Test Game',
                'title': 'Test Stream',
                'tags': ['tag-one', 'tag-two'],
                'content_classification_labels': ['MatureGame'],
                'is_branded_content': True,
            }],
        }

        with patch.object(
            self.api,
            '_get',
            return_value=response,
        ) as mock_get:

            result = self.api.channel(12345)

        mock_get.assert_called_once_with(
            'helix/channels',
            {'broadcaster_id': 12345},
        )

        self.assertEqual(result, {
            'broadcaster_id': 12345,
            'broadcaster_login': 'test_user',
            'broadcaster_name': 'Test User',
            'broadcaster_language': 'en',
            'game_id': 67890,
            'game_name': 'Test Game',
            'title': 'Test Stream',
            'tags': ['tag-one', 'tag-two'],
            'content_classification_labels': ['MatureGame'],
            'is_branded_content': True,
        })

    def test_channel_get_failure(self):
        with patch.object(self.api, '_get', return_value=False), \
             patch('brb_timer.OBS.error') as mock_error:

            result = self.api.channel(12345)

        self.assertFalse(result)

        mock_error.assert_called_once_with(
            'helix/channels failed for broadcaster_id: 12345'
        )

    def test_channel_no_data(self):
        response = {
            'data': [],
        }

        with patch.object(self.api, '_get', return_value=response), \
            patch('brb_timer.OBS.error') as mock_error:

            result = self.api.channel(12345)

        self.assertFalse(result)

        mock_error.assert_called_once_with(
            'No channel data returned for broadcaster_id: 12345'
        )

    def test_channel_falsy_first_data_item(self):
        response = {
            'data': [False],
        }

        with patch.object(self.api, '_get', return_value=response), \
            patch('brb_timer.OBS.error') as mock_error:

            result = self.api.channel(12345)

        self.assertFalse(result)

        mock_error.assert_called_once_with(
            'No channel data returned for broadcaster_id: 12345'
        )

    def test_channel_first_data_item_exists(self):
        response = {
            'data': [{
                'broadcaster_id': '12345',
            }],
        }

        with patch.object(self.api, '_get', return_value=response):
            result = self.api.channel(12345)

        self.assertEqual(result['broadcaster_id'], 12345)

    def test_server(self):
        self.assertEqual(
            self.api._server('oauth2/validate'),
            'https://id.twitch.tv',
        )

        self.assertEqual(
            self.api._server('helix/channels'),
            'https://api.twitch.tv',
        )

        self.assertEqual(
            self.api._server('anything'),
            'https://api.twitch.tv',
        )

    def test_headers(self):
        with patch('brb_timer.SCRIPT_NAME', 'TestScript'), \
             patch('brb_timer.SCRIPT_VERSION', '1.2.3'):

            headers = self.api._headers()

        self.assertEqual(headers, {
            'User-Agent': 'TestScript v1.2.3',
            'Accept': 'application/json',
            'Authorization': 'Bearer test-token',
            'Client-ID': 'test-client-id',
        })

    def test_headers_extra(self):
        extra = {
            'X-Test-Header': 'test-value',
            'Client-ID': 'overridden-client-id',
        }

        headers = self.api._headers(extra)

        self.assertEqual(headers['X-Test-Header'], 'test-value')
        self.assertEqual(
            headers['Client-ID'],
            'overridden-client-id',
        )

    def test_get_success(self):
        response_data = {
            'login': 'test_user',
            'user_id': '12345',
        }
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(response_data).encode()

        # json.load() expects a file-like object. Mock the JSON loader
        # rather than depending on urllib's response implementation.
        with patch(
            'brb_timer.urllib.request.urlopen',
            return_value=response,
        ) as mock_urlopen, \
        patch(
            'brb_timer.json.load',
            return_value=response_data,
        ) as mock_json_load:

            result = self.api._get(
                'oauth2/validate',
                {'foo': 'bar'},
            )

        self.assertEqual(result, response_data)
        mock_urlopen.assert_called_once()

        request = mock_urlopen.call_args.args[0]

        self.assertEqual(
            request.full_url,
            'https://id.twitch.tv/oauth2/validate?foo=bar',
        )
        self.assertEqual(
            request.get_header('Authorization'),
            'Bearer test-token',
        )
        self.assertEqual(
            request.get_header('Client-id'),
            'test-client-id',
        )

        mock_json_load.assert_called_once_with(response)

    def test_get_http_error(self):
        error = urllib.error.HTTPError(
            url='https://api.twitch.tv/helix/test',
            code=500,
            msg='Internal Server Error',
            hdrs={},
            fp=io.BytesIO(),
        )
        self.addCleanup(error.close)

        with patch(
            'brb_timer.urllib.request.urlopen',
            side_effect=error,
        ), patch(
            'brb_timer.OBS.error'
        ) as mock_error:
            result = self.api._get('helix/test')

        self.assertFalse(result)
        mock_error.assert_called_once()

    def test_get_unauthorized_with_detail(self):
        body = io.BytesIO(
            b'{"message": "Invalid OAuth token"}'
        )
        error = urllib.error.HTTPError(
            url='https://api.twitch.tv/helix/test',
            code=401,
            msg='Unauthorized',
            hdrs={},
            fp=body,
        )
        self.addCleanup(error.close)

        with patch(
            'brb_timer.urllib.request.urlopen',
            side_effect=error,
        ), patch(
            'brb_timer.OBS.error',
        ) as mock_error:
            result = self.api._get('helix/test')

        self.assertFalse(result)
        mock_error.assert_called_once_with(
            {'message': 'Invalid OAuth token'}
        )

    def test_get_unauthorized_without_detail(self):
        error = urllib.error.HTTPError(
            url='https://api.twitch.tv/helix/test',
            code=401,
            msg='Unauthorized',
            hdrs={},
            fp=None,
        )
        self.addCleanup(error.close)

        with patch(
            'brb_timer.urllib.request.urlopen',
            side_effect=error,
        ), patch(
            'brb_timer.json.load',
            side_effect=Exception('invalid response body'),
        ), patch(
            'brb_timer.OBS.error',
        ) as mock_error:

            result = self.api._get('helix/test')

        self.assertFalse(result)
        mock_error.assert_called_once_with('unauthorized')

    def test_get_rate_limited(self):
        headers = {
            'Ratelimit-Limit': '800',
            'Ratelimit-Remaining': '799',
            'Ratelimit-Reset': '1234567890',
        }

        error = urllib.error.HTTPError(
            url='https://api.twitch.tv/helix/test',
            code=429,
            msg='Too Many Requests',
            hdrs=headers,
            fp=io.BytesIO(),
        )
        self.addCleanup(error.close)

        reset = Mock()
        reset.__str__ = Mock(return_value='2026-09-04 12:00:00')

        with patch(
            'brb_timer.urllib.request.urlopen',
            side_effect=error,
        ), patch(
            'brb_timer.DT.epoch_secs_to_dt',
            return_value=reset,
        ) as mock_epoch, patch(
            'brb_timer.OBS.error',
        ) as mock_error:

            result = self.api._get('helix/test')

        self.assertFalse(result)

        mock_epoch.assert_called_once_with('1234567890')

        mock_error.assert_called_once_with(
            'Request for https://api.twitch.tv/helix/test was rate limited. '
            '(799/800 requests remaining. '
            'Reset at 2026-09-04 12:00:00.)'
        )

    def test_get_extra_headers(self):
        response_data = {'ok': True}

        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            'brb_timer.urllib.request.urlopen',
            return_value=response,
        ) as mock_urlopen, patch(
            'brb_timer.json.load',
            return_value=response_data,
        ):

            result = self.api._get(
                'helix/test',
                extra_headers={'X-Test': 'value'},
            )

        self.assertEqual(result, response_data)

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header('X-test'),
            'value',
        )

if __name__ == '__main__':
    unittest.main()
