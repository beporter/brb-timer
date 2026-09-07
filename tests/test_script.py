import unittest
from unittest.mock import MagicMock, Mock, patch

import brb_timer

class TestScript(unittest.TestCase):
    """
    Test names are not 100% accurate here. We're testing the
    OBS `script_*()` hook methods that the app uses to interact
    with the script.
    """

    def setUp(self):
        # Preserve the module-global script so individual tests can
        # replace it without contaminating other tests.
        self.original_script = brb_timer.script

        self.script = MagicMock()
        brb_timer.script = self.script

    def tearDown(self):
        brb_timer.script = self.original_script

    def test_module_logging(self):
        """
        The module should configure logging at import time.

        We don't reload the module here because doing so would mutate
        module-global state and make the other hook tests unnecessarily
        fragile. Instead, verify that the module exposes a configured
        logger hierarchy at the expected level.
        """
        import logging

        self.assertEqual(
            logging.getLogger().level,
            logging.DEBUG,
        )

    def test_dispatch(self):
        """
        dispatch() should return a callable which invokes the original
        callback with positional and keyword arguments unchanged.
        """
        callback = Mock(return_value="callback result")
        callback.__qualname__ = 'CALLBACK'

        with patch("brb_timer.OBS.debug") as debug:
            wrapped = brb_timer.dispatch(callback)

            self.assertTrue(callable(wrapped))

            result = wrapped(
                "first",
                "second",
                keyword="value",
            )

        self.assertEqual(result, "callback result")

        callback.assert_called_once_with(
            "first",
            "second",
            keyword="value",
        )

        # Creation and invocation should both have generated logging.
        self.assertEqual(debug.call_count, 3)

        debug.assert_any_call(
            f"Creating closure for: {callback.__qualname__}"
        )
        debug.assert_any_call(
            f"Triggering callback: {callback.__qualname__}"
        )
        debug.assert_any_call(
            f"Callback {callback.__qualname__} returning: callback result"
        )

    def test_dispatch_propagates_callback_exception(self):
        """
        dispatch() should not swallow exceptions raised by the wrapped
        callback.
        """
        callback = Mock(side_effect=ValueError("boom"))
        callback.__qualname__ = 'CALLBACK'

        with patch("brb_timer.OBS.debug") as debug:
            wrapped = brb_timer.dispatch(callback)

            with self.assertRaises(ValueError) as raised:
                wrapped("argument")

        self.assertEqual(str(raised.exception), "boom")
        callback.assert_called_once_with("argument")

    def test_script_description(self):
        desc_str = brb_timer.script_description()
        expected = (
            'BRB Timer',
            'https://github.com/beporter/brb-timer',
            brb_timer.COMMANDS.BRB,
            brb_timer.COMMANDS.AT,
            brb_timer.COMMANDS.BACK,
        )

        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, desc_str)

    def test_script_defaults(self):
        settings = MagicMock()

        with patch("brb_timer.OBS.data_set_default") as data_set_default:
            brb_timer.script_defaults(settings)

            data_set_default.assert_called_once_with(
                settings,
                "auto_hide_secs",
                brb_timer.DEFAULTS.AUTO_HIDE_SECS,
            )

    def test_script_properties(self):
        props = MagicMock()
        self.script.on_props.return_value = props

        with patch("brb_timer.OBS.debug") as debug:
            result = brb_timer.script_properties()

        self.assertIs(result, props)

        self.script.on_props.assert_called_once_with()

        debug.assert_called_once_with(
            f"{brb_timer.SCRIPT_NAME} properties generated."
        )

    def test_script_properties_failure(self):
        self.script.on_props.return_value = None

        with patch("brb_timer.OBS.debug") as debug:
            result = brb_timer.script_properties()

        self.assertIsNone(result)

        self.script.on_props.assert_called_once_with()

        debug.assert_called_once_with(
            f"{brb_timer.SCRIPT_NAME} property generation failed."
        )

    def test_script_load(self):
        settings = MagicMock()

        with patch("brb_timer.OBS.debug") as debug:
            result = brb_timer.script_load(settings)

        self.assertIsNone(result)

        self.script.on_load.assert_called_once_with(settings)

        debug.assert_called_once_with(
            f"{brb_timer.SCRIPT_NAME} loaded."
        )

    def test_script_update(self):
        settings = MagicMock()

        with patch("brb_timer.OBS.debug") as debug:
            result = brb_timer.script_update(settings)

        self.assertIsNone(result)

        self.script.on_update.assert_called_once_with(settings)

        debug.assert_called_once_with(
            f"{brb_timer.SCRIPT_NAME} settings updated."
        )

    def test_script_save(self):
        settings = MagicMock()

        with patch("brb_timer.OBS.debug") as debug:
            result = brb_timer.script_save(settings)

        self.assertIsNone(result)

        self.script.settings_merge.assert_called_once_with(settings)

        debug.assert_called_once_with(
            f"{brb_timer.SCRIPT_NAME} settings saved."
        )

    def test_script_unload(self):
        with patch("brb_timer.OBS.debug") as debug:
            result = brb_timer.script_unload()

        self.assertIsNone(result)

        self.script.on_unload.assert_called_once_with()

        debug.assert_called_once_with(
            f"{brb_timer.SCRIPT_NAME} unloaded."
        )

if __name__ == '__main__':
    unittest.main()
