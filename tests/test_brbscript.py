import datetime
import unittest
from collections import defaultdict
from unittest.mock import MagicMock, Mock, patch

import brb_timer
from brb_timer import BRBScript

class TestBRBScript(unittest.TestCase):

    def setUp(self):
        # Capture ALL log messages generated during testing.
        self.obs_patch = patch("brb_timer.OBS")
        self.obs = self.obs_patch.start()

        # Keep each test independent of BRBScript's class-level state.
        self.script = BRBScript(self.obs)

        self.settings = MagicMock()
        self.settings.twitch_oauth_access_token = ""
        self.settings.twitch_username = "streamer"
        self.settings.auto_hide_secs = 30

        self.twitch = MagicMock()
        self.guesses = MagicMock()
        self.renderer = MagicMock()
        self.irc = MagicMock()

        self.script.settings = self.settings
        self.script.twitch = self.twitch
        self.script.guesses = self.guesses
        self.script.renderer = self.renderer
        self.script.irc = self.irc
        self.script.frontend_ready = False
        self.script.events = defaultdict(list)

    def tearDown(self):
        self.obs_patch.stop()
        self.script.events = defaultdict(list)

    # ------------------------------------------------------------------
    # Basic lifecycle / state
    # ------------------------------------------------------------------

    def test_init(self):
        obs_module = object()
        script = BRBScript(obs_module)

        self.assertIs(script.obs, obs_module)

    def test_reset(self):
        old_settings = self.script.settings
        old_twitch = self.script.twitch
        old_guesses = self.script.guesses
        old_renderer = self.script.renderer

        obs_settings = MagicMock()

        with patch("brb_timer.BRBSettings") as Settings:
            with patch("brb_timer.TwitchOAuth") as Twitch:
                with patch("brb_timer.GuessManager") as Guess:
                    script = BRBScript(self.obs)
                    script.frontend_ready = True
                    script.events[123].append(Mock())

                    script.reset(obs_settings)

                    self.assertIsNot(script.settings, old_settings)
                    self.assertIsNot(script.twitch, old_twitch)
                    self.assertIsNot(script.guesses, old_guesses)
                    self.assertIsNone(script.renderer)

                    Settings.return_value.from_data.assert_called_once_with(
                        obs_settings
                    )
                    Twitch.assert_any_call(script.settings)
                    self.assertIsInstance(script.events, defaultdict)
                    self.assertEqual(len(script.events), 0)

    def test_settings_merge(self):
        incoming = MagicMock()

        self.script.settings_merge(incoming)

        self.settings.from_data.assert_called_once_with(incoming)
        self.settings.to_data.assert_called_once_with(incoming)

    def test_is_frontend_ready(self):
        self.script.frontend_ready = False
        self.assertFalse(self.script.is_frontend_ready())

        self.script.frontend_ready = True
        self.assertTrue(self.script.is_frontend_ready())

    def test_is_source_exists(self):
        with patch("brb_timer.OBS.source_exists") as source_exists:
            source_exists.return_value = True

            self.script.frontend_ready = False
            self.assertFalse(self.script.is_source_exists())
            source_exists.assert_not_called()

            self.script.frontend_ready = True
            self.assertTrue(self.script.is_source_exists())

            source_exists.return_value = False
            self.assertFalse(self.script.is_source_exists())

    def test_is_renderer_initialized(self):
        self.script.renderer = None
        self.assertFalse(self.script.is_renderer_initialized())

        self.script.renderer = MagicMock()
        self.assertTrue(self.script.is_renderer_initialized())

    def test_is_renderer_visible(self):
        self.script.renderer = None
        self.assertFalse(self.script.is_renderer_visible())

        self.script.renderer = MagicMock()
        self.script.renderer.visible.return_value = False
        self.assertFalse(self.script.is_renderer_visible())

        self.script.renderer.visible.return_value = True
        self.assertTrue(self.script.is_renderer_visible())

    def test_is_guessing_initialized(self):
        self.script.guesses = None
        self.assertFalse(self.script.is_guessing_initialized())

        self.script.guesses = MagicMock()
        self.assertTrue(self.script.is_guessing_initialized())

    def test_is_guessing_running(self):
        self.script.guesses = None
        self.assertFalse(self.script.is_guessing_running())

        self.script.guesses = MagicMock()
        self.script.guesses.running.return_value = False
        self.assertFalse(self.script.is_guessing_running())

        self.script.guesses.running.return_value = True
        self.assertTrue(self.script.is_guessing_running())

    def test_is_irc_initialized(self):
        self.script.irc = None
        self.assertFalse(self.script.is_irc_initialized())

        self.script.irc = MagicMock()
        self.assertTrue(self.script.is_irc_initialized())

    def test_is_irc_running(self):
        self.script.irc = None
        self.assertFalse(self.script.is_irc_running())

        self.script.irc = MagicMock()
        self.script.irc.running = False
        self.assertFalse(self.script.is_irc_running())

        self.script.irc.running = True
        self.assertTrue(self.script.is_irc_running())

    def test_is_irc_connected(self):
        self.script.irc = None
        self.assertFalse(self.script.is_irc_connected())

        self.script.irc = MagicMock()
        self.script.irc.running = False
        self.assertFalse(self.script.is_irc_connected())

        self.script.irc.running = True
        self.script.irc.thread.is_alive.return_value = False
        self.assertFalse(self.script.is_irc_connected())

        self.script.irc.thread.is_alive.return_value = True
        self.assertTrue(self.script.is_irc_connected())

    def test_is_twitch_initialized(self):
        self.script.twitch = None
        self.assertFalse(self.script.is_twitch_initialized())

        self.script.twitch = MagicMock()
        self.assertTrue(self.script.is_twitch_initialized())

    def test_is_twitch_present(self):
        self.script.twitch = None
        self.assertFalse(self.script.is_twitch_present())

        self.script.twitch = MagicMock()
        self.script.twitch.creds_present.return_value = False
        self.assertFalse(self.script.is_twitch_present())

        self.script.twitch.creds_present.return_value = True
        self.assertTrue(self.script.is_twitch_present())

    def test_is_twitch_valid(self):
        self.script.twitch = None
        self.assertFalse(self.script.is_twitch_valid())

        self.script.twitch = MagicMock()
        self.script.twitch.creds_present.return_value = False
        self.assertFalse(self.script.is_twitch_valid())

        self.script.twitch.creds_present.return_value = True
        self.script.twitch.creds_expired.return_value = True
        self.assertFalse(self.script.is_twitch_valid())

        self.script.twitch.creds_expired.return_value = False
        self.assertTrue(self.script.is_twitch_valid())

    def test_is_twitch_populated(self):
        self.script.twitch = None
        self.assertFalse(self.script.is_twitch_populated())

        self.script.twitch = MagicMock()
        self.script.twitch.creds_present.return_value = True
        self.script.twitch.creds_expired.return_value = False
        self.script.twitch.user_present.return_value = False
        self.script.twitch.channel_present.return_value = True
        self.assertFalse(self.script.is_twitch_populated())

        self.script.twitch.user_present.return_value = True
        self.script.twitch.channel_present.return_value = False
        self.assertFalse(self.script.is_twitch_populated())

        self.script.twitch.channel_present.return_value = True
        self.assertTrue(self.script.is_twitch_populated())

    def test_is_timer_ticking(self):
        with patch("brb_timer.OBS.timer_running") as timer_running:
            timer_running.return_value = False
            self.assertFalse(self.script.is_timer_ticking())
            timer_running.assert_called_once_with(self.script.on_timer)

            timer_running.reset_mock()
            timer_running.return_value = True
            self.assertTrue(self.script.is_timer_ticking())
            timer_running.assert_called_once_with(self.script.on_timer)

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------

    def test_update_prop_visibility(self):
        props = MagicMock()

        self.script.frontend_ready = True
        self.renderer.visible.return_value = True
        self.guesses.running.return_value = True
        self.irc.running = True
        self.irc.thread.is_alive.return_value = True
        self.twitch.creds_present.return_value = True
        self.twitch.creds_expired.return_value = False

        with patch.object(
            self.script,
            "is_source_exists",
            return_value=True,
        ), patch.object(
            self.script,
            "is_timer_ticking",
            return_value=False,
        ), patch(
            "brb_timer.OBS.properties_visibility_set"
        ) as visibility_set:

            result = self.script.update_prop_visibility(props)

        self.assertTrue(result)

        visibility = visibility_set.call_args.args[1]

        self.assertTrue(visibility["twitch_connect_button"])
        self.assertTrue(visibility["twitch_oauth_access_token"])
        self.assertFalse(visibility["twitch_creds_expired"])

        self.assertFalse(visibility["make_ready_button"])
        self.assertTrue(visibility["make_not_ready_button"])
        self.assertFalse(visibility["create_source_button"])

        self.assertFalse(visibility["irc_start_button"])
        self.assertTrue(visibility["irc_stop_button"])

        self.assertFalse(visibility["guess_start_button"])
        self.assertTrue(visibility["guess_stop_button"])

        self.assertTrue(visibility["timer_start_button"])
        self.assertFalse(visibility["timer_stop_button"])

    def test_on_props(self):
        props = MagicMock()
        factory = MagicMock()
        factory.props = props

        with patch("brb_timer.OBS.properties_create", return_value=props), \
             patch("brb_timer.OBSPropsFactory", return_value=factory), \
             patch.object(
                 self.script,
                 "is_frontend_ready",
                 return_value=False,
             ), \
             patch.object(
                 self.script,
                 "is_source_exists",
                 return_value=False,
             ), \
             patch.object(
                 self.script,
                 "is_irc_connected",
                 return_value=False,
             ), \
             patch.object(
                 self.script,
                 "is_guessing_running",
                 return_value=False,
             ), \
             patch.object(
                 self.script,
                 "is_timer_ticking",
                 return_value=False,
             ):

            result = self.script.on_props()

        self.assertIs(result, props)

        factory.twitch_button.assert_called_once()
        factory.text.assert_called_once()
        factory.int_with_unit.assert_called_once()

        self.assertEqual(factory.button.call_count, 9)

    # ------------------------------------------------------------------
    # Twitch
    # ------------------------------------------------------------------

    def test_update_twitch(self):
        # No effective change.
        self.settings.twitch_oauth_access_token = "token"

        self.assertTrue(
            self.script.update_twitch(
                {"twitch_oauth_access_token": "token"}
            )
        )

        self.twitch.user_get.assert_not_called()
        self.twitch.channel_get.assert_not_called()

        # No changes object.
        self.twitch.reset_mock()
        self.assertTrue(self.script.update_twitch())
        self.twitch.user_get.assert_not_called()

        # Token removed.
        self.twitch.reset_mock()
        self.settings.twitch_oauth_access_token = "old"

        with patch("brb_timer.OBS.warn") as warn:
            result = self.script.update_twitch(
                {"twitch_oauth_access_token": ""}
            )

        self.assertFalse(result)
        self.twitch.clear_settings.assert_called_once()
        warn.assert_called_once()

        # New token but creds are unavailable.
        self.twitch.reset_mock()
        self.settings.twitch_oauth_access_token = ""

        self.twitch.creds_present.return_value = False

        with patch("brb_timer.DT.timestamp", return_value=1000), \
             patch("brb_timer.OBS.warn") as warn:

            result = self.script.update_twitch(
                {"twitch_oauth_access_token": "new-token"}
            )

        self.assertFalse(result)
        self.twitch.creds_set.assert_called_once_with(
            "new-token",
            1120,
        )
        warn.assert_called_once()

        # Valid credentials, but user lookup fails.
        self.twitch.reset_mock()
        self.twitch.creds_present.return_value = True
        self.twitch.user_get.return_value = False

        with patch("brb_timer.DT.timestamp", return_value=1000), \
             patch("brb_timer.OBS.error") as error:

            result = self.script.update_twitch(
                {"twitch_oauth_access_token": "new-token"}
            )

        self.assertFalse(result)
        self.twitch.user_get.assert_called_once()
        self.twitch.channel_get.assert_not_called()
        error.assert_called_once()

        # User lookup succeeds, channel lookup fails.
        self.twitch.reset_mock()
        self.twitch.creds_present.return_value = True
        self.twitch.user_get.return_value = True
        self.twitch.channel_get.return_value = False

        with patch("brb_timer.DT.timestamp", return_value=1000), \
             patch("brb_timer.OBS.error") as error:

            result = self.script.update_twitch(
                {"twitch_oauth_access_token": "new-token"}
            )

        self.assertFalse(result)
        self.twitch.user_get.assert_called_once()
        self.twitch.channel_get.assert_called_once()
        error.assert_called_once()

        # Everything succeeds.
        self.twitch.reset_mock()
        self.twitch.creds_present.return_value = True
        self.twitch.user_get.return_value = True
        self.twitch.channel_get.return_value = True

        with patch("brb_timer.DT.timestamp", return_value=1000), \
             patch("brb_timer.OBS.info") as info:

            result = self.script.update_twitch(
                {"twitch_oauth_access_token": "new-token"}
            )

        self.assertTrue(result)
        self.twitch.creds_set.assert_called_once_with(
            "new-token",
            1120,
        )
        self.twitch.user_get.assert_called_once()
        self.twitch.channel_get.assert_called_once()
        info.assert_called_once()

    # ------------------------------------------------------------------
    # Renderer
    # ------------------------------------------------------------------

    def test_update_renderer(self):
        self.script.frontend_ready = False

        self.assertFalse(self.script.update_renderer("Timer"))

        self.script.frontend_ready = True
        old_renderer = MagicMock()
        self.script.renderer = old_renderer

        with patch("brb_timer.OBS.source_exists", return_value=True), \
             patch("brb_timer.TimerRenderer") as Renderer:

            new_renderer = Renderer.return_value

            result = self.script.update_renderer("New Timer")

        self.assertTrue(result)
        old_renderer.clear.assert_called_once()
        old_renderer.set_source.assert_called_once_with("New Timer")
        Renderer.assert_called_once_with("New Timer")
        self.assertIs(self.script.renderer, new_renderer)

        # Existing renderer + nonexistent source.
        old_renderer = MagicMock()
        self.script.renderer = old_renderer

        with patch("brb_timer.OBS.source_exists", return_value=False), \
             patch("brb_timer.TimerRenderer") as Renderer:

            self.assertTrue(self.script.update_renderer("Missing"))

        old_renderer.clear.assert_not_called()
        old_renderer.set_source.assert_not_called()
        Renderer.assert_called_once_with("Missing")

        # No existing renderer.
        self.script.renderer = None

        with patch("brb_timer.OBS.source_exists", return_value=True), \
             patch("brb_timer.TimerRenderer") as Renderer:

            self.assertTrue(self.script.update_renderer("Timer"))

        Renderer.assert_called_once_with("Timer")

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def test_event_router(self):
        callback1 = Mock()
        callback2 = Mock()

        self.script.events[123] = [callback1, callback2]

        with patch("brb_timer.OBS.event_name", return_value="TEST") as event_name:
            self.script.event_router(123)

        callback1.assert_called_once()
        callback2.assert_called_once()
        event_name.assert_called_once_with(123)

        callback1.reset_mock()
        callback2.reset_mock()

        self.script.event_router(456)

        callback1.assert_not_called()
        callback2.assert_not_called()

    def test_event_add(self):
        callback = Mock()
        callback.__name__ = 'TEST'

        with patch("brb_timer.OBS.event_name", return_value="TEST"):
            result = self.script.event_add(123, callback)

        # The implementation currently has no explicit return.
        self.assertIsNone(result)
        self.assertEqual(len(self.script.events[123]), 1)

        self.script.events[123][0]()
        callback.assert_called_once()

    def test_event_start_ticking(self):
        with patch.object(self.script, "event_stop_ticking") as stop, \
             patch("brb_timer.OBS.timer_add") as timer_add:

            self.script.event_start_ticking()

        stop.assert_called_once()
        timer_add.assert_called_once_with(self.script.on_timer, 1000)

    def test_event_start_ticking_already_running(self):
        with patch.object(self.script, "event_stop_ticking"), \
             patch(
                 "brb_timer.OBS.timer_add",
                 side_effect=ValueError,
             ), \
             patch("brb_timer.OBS.warn") as warn:

            self.script.event_start_ticking()

        warn.assert_called_once()

    def test_event_stop_ticking(self):
        with patch("brb_timer.OBS.timer_remove") as timer_remove:
            self.script.event_stop_ticking()

        timer_remove.assert_called_once_with(self.script.on_timer, True)

    def test_event_stop_ticking_not_running(self):
        with patch(
            "brb_timer.OBS.timer_remove",
            side_effect=ValueError,
        ), patch("brb_timer.OBS.warn") as warn:

            self.script.event_stop_ticking()

        warn.assert_called_once()

    # ------------------------------------------------------------------
    # OBS lifecycle
    # ------------------------------------------------------------------

    def test_on_load(self):
        settings = MagicMock()

        with patch.object(self.script, "reset") as reset, \
             patch("brb_timer.OBS.event_register_router") as register, \
             patch("brb_timer.OBS.event_name", side_effect=str), \
             patch("brb_timer.OBS.data_get_json", return_value="{}"):

            self.script.on_load(settings)

        self.assertFalse(self.script.frontend_ready)
        reset.assert_called_once_with(settings)
        register.assert_called_once_with(self.script.event_router)

        self.assertEqual(len(self.script.events), 3)

        self.assertIn(
            self.obs.OBS_FRONTEND_EVENT_FINISHED_LOADING,
            self.script.events,
        )
        self.assertIn(
            self.obs.OBS_FRONTEND_EVENT_STREAMING_STARTING,
            self.script.events,
        )
        self.assertIn(
            self.obs.OBS_FRONTEND_EVENT_STREAMING_STOPPING,
            self.script.events,
        )

    def test_on_obs_ready(self):
        self.script.frontend_ready = False

        with patch("brb_timer.OBS.source_exists", return_value=False), \
             patch.object(self.script, "on_create_source") as create_source, \
             patch("brb_timer.OBS.info"):

            self.script.on_obs_ready()

        self.assertTrue(self.script.frontend_ready)
        create_source.assert_called_once()

        # Existing source means no creation.
        self.script.frontend_ready = False

        with patch("brb_timer.OBS.source_exists", return_value=True), \
             patch.object(self.script, "on_create_source") as create_source:

            self.script.on_obs_ready()

        self.assertTrue(self.script.frontend_ready)
        create_source.assert_not_called()

    def test_on_update(self):
        changed = MagicMock()
        incoming = {
            "twitch_oauth_access_token": "token",
        }

        with patch("brb_timer.OBS.data_get_all", return_value=incoming), \
             patch.object(
                 self.script,
                 "update_twitch",
                 return_value=True,
             ) as update_twitch:

            result = self.script.on_update(changed)

        self.assertTrue(result)
        update_twitch.assert_called_once_with(incoming)
        self.settings.to_data.assert_called_once_with(changed)

    # ------------------------------------------------------------------
    # Source creation
    # ------------------------------------------------------------------

    def test_on_create_source(self):
        # Not ready.
        self.script.frontend_ready = False

        with patch("brb_timer.OBS.warn") as warn:
            result = self.script.on_create_source()

        self.assertFalse(result)
        warn.assert_called_once()

        # Existing source.
        self.script.frontend_ready = True

        with patch("brb_timer.OBS.source_exists", return_value=True), \
             patch("brb_timer.OBS.info") as info:

            result = self.script.on_create_source()

        self.assertTrue(result)
        info.assert_called_once()

        # New source, successful creation.
        self.script.frontend_ready = True
        generator = MagicMock()
        generator.create_source.return_value = True

        with patch("brb_timer.OBS.source_exists", return_value=False), \
             patch("brb_timer.SourceGenerator", return_value=generator), \
             patch("brb_timer.OBS.frontend_open_source_props"), \
             patch.object(self.script, "update_prop_visibility"):

            result = self.script.on_create_source()

        self.assertTrue(result)
        generator.create_source.assert_called_once()

        # New source, failed creation.
        generator = MagicMock()
        generator.create_source.return_value = False

        with patch("brb_timer.OBS.source_exists", return_value=False), \
             patch("brb_timer.SourceGenerator", return_value=generator), \
             patch("brb_timer.OBS.error") as error:

            result = self.script.on_create_source()

        self.assertFalse(result)
        error.assert_called_once()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def test_on_streaming_starting(self):
        self.script.frontend_ready = False

        with patch.object(
            self.script,
            "update_twitch",
            return_value=False,
        ) as update_twitch, \
             patch("brb_timer.OBS.warn") as warn:

            self.script.on_streaming_starting()

        self.assertTrue(self.script.frontend_ready)
        update_twitch.assert_called_once()
        warn.assert_called_once()

    def test_on_streaming_starting_success(self):
        self.twitch.channel_get.return_value = "channel"
        self.twitch.user_get.return_value = {"name": "bot"}

        self.settings.twitch_oauth_access_token = "oauth-token"

        with patch.object(
            self.script,
            "update_twitch",
            return_value=True,
        ), patch.object(
            self.script,
            "update_renderer",
            return_value=True,
        ) as update_renderer, patch(
            "brb_timer.TwitchIRCClient"
        ) as IRCClient:

            self.script.on_streaming_starting()

        self.assertTrue(self.script.frontend_ready)

        update_renderer.assert_called_once_with(
            brb_timer.SOURCES.TEXT_TIMER
        )

        self.renderer.hide.assert_called_once()

        IRCClient.assert_called_once_with(
            "channel",
            "bot",
            "oauth-token",
            self.script.on_chat,
        )

        IRCClient.return_value.start.assert_called_once()
        self.assertIs(self.script.irc, IRCClient.return_value)

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------

    def test_on_timer(self):
        self.guesses.should_hide.return_value = False
        self.renderer.visible.return_value = True
        self.guesses.timer_str.return_value = "00:42"

        with patch("brb_timer.OBS.event_remove_self") as remove_self:
            self.script.on_timer()

        remove_self.assert_not_called()
        self.guesses.timer_str.assert_called_once()
        self.renderer.set_text.assert_called_once_with("00:42")

    def test_on_timer_hide(self):
        self.guesses.should_hide.return_value = True
        self.renderer.visible.return_value = False

        with patch("brb_timer.OBS.event_remove_self") as remove_self:
            self.script.on_timer()

        self.renderer.hide.assert_called_once()
        remove_self.assert_called_once()

    def test_on_timer_hide_without_renderer(self):
        self.script.renderer = None
        self.guesses.should_hide.return_value = True

        with patch("brb_timer.OBS.event_remove_self") as remove_self:
            self.script.on_timer()

        remove_self.assert_called_once()

    # ------------------------------------------------------------------
    # Streaming stop / unload
    # ------------------------------------------------------------------

    def test_on_streaming_stopping(self):
        self.script.irc = MagicMock()
        self.script.irc.running = True

        with patch.object(
            self.script,
            "event_stop_ticking",
        ) as stop_timer:

            self.script.on_streaming_stopping()

        self.guesses.end.assert_called_once_with(0)
        stop_timer.assert_called_once()
        self.script.irc.stop.assert_called_once()

    def test_on_streaming_stopping_without_irc(self):
        self.script.irc = None

        with patch.object(
            self.script,
            "event_stop_ticking",
        ) as stop_timer:

            self.script.on_streaming_stopping()

        self.guesses.end.assert_called_once_with(0)
        stop_timer.assert_called_once()

    def test_on_unload(self):
        with patch("brb_timer.OBS.event_unregister_router") as unregister:
            self.script.events[123].append(Mock())

            self.script.on_unload()

        unregister.assert_called_once_with(self.script.event_router)
        self.assertEqual(self.script.events, {})

    # ------------------------------------------------------------------
    # Property callbacks
    # ------------------------------------------------------------------

    def test_on_twitch_oauth_access_token(self):
        props = MagicMock()
        prop = MagicMock()
        settings = MagicMock()
        incoming = {"twitch_oauth_access_token": "token"}

        with patch(
            "brb_timer.OBS.data_get_all",
            return_value=incoming,
        ), patch.object(
            self.script,
            "update_twitch",
            return_value=True,
        ) as update_twitch, patch.object(
            self.script,
            "update_prop_visibility",
        ) as visibility:

            result = self.script.on_twitch_oauth_access_token(
                props,
                prop,
                settings,
            )

        self.assertTrue(result)
        update_twitch.assert_called_once_with(incoming)
        visibility.assert_called_once_with(props)

    def test_on_auto_hide_secs(self):
        props = MagicMock()
        prop = MagicMock()
        obs_settings = MagicMock()

        with patch(
            "brb_timer.OBS.data_get",
            return_value="45",
        ):
            result = self.script.on_auto_hide_secs(
                props,
                prop,
                obs_settings,
            )

        self.assertFalse(result)
        self.assertEqual(self.settings.auto_hide_secs, 45)

    # ------------------------------------------------------------------
    # Chat routing
    # ------------------------------------------------------------------

    def test_on_chat(self):
        messages = {
            "brb": MagicMock(),
            "at": MagicMock(),
            "back": MagicMock(),
            "unknown": MagicMock(),
        }

        messages["brb"].message = "!brb"
        messages["at"].message = "!at 1:23"
        messages["back"].message = "!back"
        messages["unknown"].message = "!something"

        with patch.object(self.script, "command_brb") as brb, \
             patch.object(self.script, "command_at") as at, \
             patch.object(self.script, "command_back") as back:

            self.script.on_chat(messages["brb"])
            self.script.on_chat(messages["at"])
            self.script.on_chat(messages["back"])
            self.script.on_chat(messages["unknown"])

        brb.assert_called_once_with(messages["brb"])
        at.assert_called_once_with(messages["at"])
        back.assert_called_once_with(messages["back"])

    # ------------------------------------------------------------------
    # !brb
    # ------------------------------------------------------------------

    def test_command_brb(self):
        msg = MagicMock()
        msg.is_mod = False
        msg.is_broadcaster = False

        self.script.command_brb(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.ONLY_MOD_START
        )
        self.guesses.start.assert_not_called()

        # Already running.
        self.irc.reset_mock()
        self.guesses.running.return_value = True
        msg.is_mod = True

        self.script.command_brb(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.ALREADY_RUNNING
        )

        # Successful start.
        self.irc.reset_mock()
        self.guesses.running.return_value = False

        with patch.object(self.script, "event_stop_ticking") as stop, \
             patch.object(self.script, "event_start_ticking") as start:

            self.script.command_brb(msg)

        stop.assert_called_once()
        self.guesses.start.assert_called_once()
        start.assert_called_once()
        self.irc.send_chat.assert_not_called()

    # ------------------------------------------------------------------
    # !at
    # ------------------------------------------------------------------

    def test_command_at(self):
        msg = MagicMock()
        msg.username = "alice"
        msg.message = "!at 1:23"
        msg.is_mod = False
        msg.is_broadcaster = False

        # No active BRB.
        self.guesses.running.return_value = False

        self.script.command_at(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.NO_BRB
        )

        # Invalid time.
        self.irc.reset_mock()
        self.guesses.running.return_value = True

        with patch.object(
            self.script,
            "_parse_at",
            return_value=False,
        ):
            self.script.command_at(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.BAD_GUESS.format(user="alice")
        )

        # Existing guess.
        self.irc.reset_mock()

        existing_guess = datetime.timedelta(seconds=80)
        self.guesses.has_guess.return_value = existing_guess

        with patch.object(
            self.script,
            "_parse_at",
            return_value=datetime.timedelta(seconds=90),
        ), patch(
            "brb_timer.DT.strfdelta",
            return_value="1:20",
        ):

            self.script.command_at(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.ALREADY_GUESSED.format(
                user="alice",
                guess="1:20",
            )
        )

        # Valid, new guess.
        self.irc.reset_mock()
        self.guesses.has_guess.return_value = None
        self.guesses.add_guess.return_value = True

        parsed = datetime.timedelta(seconds=90)

        with patch.object(
            self.script,
            "_parse_at",
            return_value=parsed,
        ), patch(
            "brb_timer.DT.strfdelta",
            return_value="1:30",
        ):

            self.script.command_at(msg)

        self.guesses.add_guess.assert_called_once_with(
            "alice",
            parsed.total_seconds(),
        )

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.GUESS_ACCEPTED.format(
                user="alice",
                guess="1:30",
            )
        )

    def test_command_at_guess_rejected(self):
        msg = MagicMock()
        msg.username = "alice"
        msg.message = "!at 1:23"

        self.guesses.running.return_value = True
        self.guesses.has_guess.return_value = None
        self.guesses.add_guess.return_value = False

        with patch.object(
            self.script,
            "_parse_at",
            return_value=datetime.timedelta(seconds=83),
        ):

            self.script.command_at(msg)

        self.guesses.add_guess.assert_called_once()
        self.irc.send_chat.assert_not_called()

    # ------------------------------------------------------------------
    # !back
    # ------------------------------------------------------------------

    def test_command_back(self):
        msg = MagicMock()
        msg.is_mod = False
        msg.is_broadcaster = False

        self.script.command_back(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.ONLY_MOD_END
        )

        # Authorized, but no BRB.
        self.irc.reset_mock()
        msg.is_mod = True
        self.guesses.running.return_value = False

        self.script.command_back(msg)

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.NO_BRB
        )

    def test_command_back_with_winner(self):
        msg = MagicMock()
        msg.is_mod = True
        msg.is_broadcaster = False

        self.guesses.running.return_value = True
        self.guesses.winner.return_value = ("alice", 80)
        self.guesses.actual_secs = 90

        elapsed = datetime.timedelta(seconds=90)
        self.guesses.elapsed_time.return_value = elapsed

        with patch.object(self.script, "event_stop_ticking"), \
             patch("brb_timer.DT.duration_secs_to_dt") as secs_to_dt, \
             patch("brb_timer.DT.duration_str", side_effect=["1:30", "0:10"]):

            self.script.command_back(msg)

        self.guesses.end.assert_called_once_with(
            self.settings.auto_hide_secs
        )

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.BRB_FINISHED.format(
                streamer="streamer",
                time="1:30",
                winner="alice",
                diff="0:10",
            )
        )

        secs_to_dt.assert_called_once_with(90)

    def test_command_back_without_winner(self):
        msg = MagicMock()
        msg.is_mod = True
        msg.is_broadcaster = False

        self.guesses.running.return_value = True
        self.guesses.winner.return_value = (False, None)
        self.guesses.actual_secs = 90

        with patch.object(self.script, "event_stop_ticking"), \
             patch(
                 "brb_timer.DT.duration_str",
                 return_value="1:30",
             ):

            self.script.command_back(msg)

        self.guesses.end.assert_called_once_with(
            self.settings.auto_hide_secs
        )

        self.irc.send_chat.assert_called_once_with(
            brb_timer.MESSAGES.BRB_FINISHED_NO_GUESSES.format(
                streamer="streamer",
                time="1:30",
            )
        )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def test_parse_at(self):
        with patch(
            "brb_timer.DT.strpdelta",
            return_value=datetime.timedelta(seconds=90),
        ) as parser:

            result = self.script._parse_at("!at 1:30")

        self.assertEqual(result, datetime.timedelta(seconds=90))
        parser.assert_called_once_with("1:30")

    def test_parse_at_invalid(self):
        with patch(
            "brb_timer.DT.strpdelta",
            return_value=False,
        ) as parser, patch("brb_timer.OBS.warn") as warn:

            result = self.script._parse_at("!at nonsense")

        self.assertFalse(result)
        parser.assert_called_once_with("nonsense")
        warn.assert_called_once()

    def test_parse_at_ignores_extra_arguments(self):
        with patch(
            "brb_timer.DT.strpdelta",
            return_value=datetime.timedelta(seconds=60),
        ):

            result = self.script._parse_at(
                "!at 1:00 some extra text"
            )

        self.assertEqual(result, datetime.timedelta(seconds=60))

if __name__ == "__main__":
    unittest.main()
