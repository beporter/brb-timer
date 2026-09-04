import unittest
from unittest.mock import MagicMock, call, patch

from brb_timer import OBSPropsFactory

class TestOBSPropsFactory(unittest.TestCase):

    def setUp(self):
        # The production class receives obspython through dependency
        # injection, so no patching of the real OBS module is necessary.
        self.mock_obs = MagicMock()
        self.mock_props = MagicMock(name="props")

        # Constants consumed by the factory.
        self.mock_obs.OBS_TEXT_DEFAULT = 100
        self.mock_obs.OBS_TEXT_INFO_NORMAL = 200
        self.mock_obs.OBS_BUTTON_URL = 300

        self.factory = OBSPropsFactory(
            self.mock_obs,
            self.mock_props,
        )

        # dispatch() is a global dependency of the implementation.
        # Have it return a predictable wrapper so we can verify that
        # the callback passed to obspython is the dispatched callback.
        self.dispatch_patcher = patch(
            "brb_timer.dispatch",
            side_effect=lambda callback: ("dispatched", callback),
        )
        self.mock_dispatch = self.dispatch_patcher.start()

    def tearDown(self):
        self.dispatch_patcher.stop()

    def test_init(self):
        self.assertIs(
            self.factory.obs,
            self.mock_obs,
        )
        self.assertIs(
            self.factory.props,
            self.mock_props,
        )

    def test_text_with_explicit_label(self):
        prop = MagicMock(name="property")
        self.mock_obs.obs_properties_add_text.return_value = prop

        callback = MagicMock(name="modified_callback")

        self.factory.text(
            "timer_text",
            label="Timer Text",
            type=123,
            modified_callback=callback,
        )

        self.mock_obs.obs_properties_add_text.assert_called_once_with(
            self.mock_props,
            "timer_text",
            "Timer Text",
            123,
        )

        self.mock_dispatch.assert_called_once_with(callback)

        self.mock_obs.obs_property_set_modified_callback.assert_called_once_with(
            prop,
            ("dispatched", callback),
        )

    def test_text_without_label_derives_label_from_name(self):
        prop = MagicMock(name="property")
        self.mock_obs.obs_properties_add_text.return_value = prop

        self.factory.text(
            "timer_text",
            type=self.mock_obs.OBS_TEXT_DEFAULT,
        )

        self.mock_obs.obs_properties_add_text.assert_called_once_with(
            self.mock_props,
            "timer_text",
            "Timer text",
            self.mock_obs.OBS_TEXT_DEFAULT,
        )

        self.mock_obs.obs_property_set_modified_callback.assert_not_called()
        self.mock_dispatch.assert_not_called()

    def test_text_without_modified_callback(self):
        prop = MagicMock(name="property")
        self.mock_obs.obs_properties_add_text.return_value = prop

        self.factory.text(
            "timer",
            "Timer",
            123,
            None,
        )

        self.mock_obs.obs_properties_add_text.assert_called_once_with(
            self.mock_props,
            "timer",
            "Timer",
            123,
        )

        self.mock_obs.obs_property_set_modified_callback.assert_not_called()
        self.mock_dispatch.assert_not_called()

    def test_text_does_not_set_callback_when_callback_is_none(self):
        prop = MagicMock(name="property")
        self.mock_obs.obs_properties_add_text.return_value = prop

        self.factory.text(
            "timer",
            label="Timer",
            modified_callback=None,
        )

        self.mock_obs.obs_property_set_modified_callback.assert_not_called()

    def test_twitch_button_with_url(self):
        button = MagicMock(name="button")

        with patch.object(
            self.factory,
            "url_button",
            return_value=button,
        ) as mock_url_button:

            result = self.factory.twitch_button(
                "channel",
                url="https://twitch.tv/example",
                text_type=123,
                long_desc="Watch the channel.",
                show=True,
            )

        self.assertIs(result, button)

        mock_url_button.assert_called_once_with(
            "twitch_channel",
            "Channel Twitch",
            "https://twitch.tv/example",
            123,
            "Watch the channel.",
        )

        self.mock_obs.obs_property_set_visible.assert_not_called()

    def test_twitch_button_with_url_hidden(self):
        button = MagicMock(name="button")

        with patch.object(
            self.factory,
            "url_button",
            return_value=button,
        ) as mock_url_button:

            result = self.factory.twitch_button(
                "channel",
                url="https://twitch.tv/example",
                text_type=self.mock_obs.OBS_TEXT_INFO_NORMAL,
                show=False,
            )

        self.assertIs(result, button)

        mock_url_button.assert_called_once_with(
            "twitch_channel",
            "Channel Twitch",
            "https://twitch.tv/example",
            self.mock_obs.OBS_TEXT_INFO_NORMAL,
            "",
        )

        self.mock_obs.obs_property_set_visible.assert_called_once_with(
            button,
            False,
        )

    def test_twitch_button_without_url(self):
        button = MagicMock(name="button")
        callback = MagicMock(name="twitch_callback")

        # The implementation gets the callback from the global `script`
        # object. Patch it at the point where brb_timer looks it up.
        with patch(
            "brb_timer.script",
            create=True,
        ) as mock_script:
            mock_script.on_twitch_channel = callback

            with patch.object(
                self.factory,
                "button",
                return_value=button,
            ) as mock_button:

                result = self.factory.twitch_button(
                    "channel",
                    text_type=123,
                    long_desc="Channel description.",
                    show=True,
                )

        self.assertIs(result, button)

        mock_button.assert_called_once_with(
            "twitch_channel",
            "Channel Twitch",
            callback,
            123,
            "Channel description.",
        )

    def test_twitch_button_without_url_hidden(self):
        button = MagicMock(name="button")
        callback = MagicMock(name="twitch_callback")

        with patch(
            "brb_timer.script",
            create=True,
        ) as mock_script:
            mock_script.on_twitch_channel = callback

            with patch.object(
                self.factory,
                "button",
                return_value=button,
            ) as mock_button:

                result = self.factory.twitch_button(
                    "channel",
                    show=False,
                )

        self.assertIs(result, button)

        mock_button.assert_called_once()
        self.mock_obs.obs_property_set_visible.assert_called_once_with(
            button,
            False,
        )

    def test_url_button(self):
        button = MagicMock(name="button")

        with patch.object(
            self.factory,
            "button",
            return_value=button,
        ) as mock_button:

            result = self.factory.url_button(
                "discord",
                "Discord",
                "https://discord.example/",
                text_type=123,
                long_desc="Join us.",
                wrap=False,
                show=True,
            )

        self.assertIs(result, button)

        mock_button.assert_called_once_with(
            "discord",
            "Discord",
            None,
            123,
            "Join us.",
            False,
        )

        self.mock_obs.obs_property_button_set_type.assert_called_once_with(
            button,
            self.mock_obs.OBS_BUTTON_URL,
        )

        self.mock_obs.obs_property_button_set_url.assert_called_once_with(
            button,
            "https://discord.example/",
        )

        self.mock_obs.obs_property_set_visible.assert_not_called()

    def test_url_button_hidden(self):
        button = MagicMock(name="button")

        with patch.object(
            self.factory,
            "button",
            return_value=button,
        ):

            result = self.factory.url_button(
                "discord",
                "Discord",
                "https://discord.example/",
                show=False,
            )

        self.assertIs(result, button)

        self.mock_obs.obs_property_button_set_type.assert_called_once_with(
            button,
            self.mock_obs.OBS_BUTTON_URL,
        )

        self.mock_obs.obs_property_button_set_url.assert_called_once_with(
            button,
            "https://discord.example/",
        )

        self.mock_obs.obs_property_set_visible.assert_called_once_with(
            button,
            False,
        )

    def test_button_with_callback(self):
        button = MagicMock(name="button")
        callback = MagicMock(name="callback")

        self.mock_obs.obs_properties_add_button.return_value = button

        result = self.factory.button(
            "start",
            "Start Timer",
            callback=callback,
            text_type=123,
            long_desc="Starts the timer.",
            wrap=False,
            show=True,
        )

        self.assertIs(result, button)

        self.mock_obs.obs_properties_add_button.assert_called_once_with(
            self.mock_props,
            "start_button",
            "Start Timer",
            ("dispatched", callback),
        )

        self.mock_dispatch.assert_called_once_with(callback)

        self.mock_obs.obs_property_set_long_description.assert_called_once_with(
            button,
            "Starts the timer.",
        )

        self.mock_obs.obs_property_set_visible.assert_not_called()

    def test_button_without_callback(self):
        button = MagicMock(name="button")
        self.mock_obs.obs_properties_add_button.return_value = button

        result = self.factory.button(
            "start",
            "Start Timer",
        )

        self.assertIs(result, button)

        # The implementation creates an anonymous no-op callback.
        self.mock_dispatch.assert_called_once()

        dispatched_callback = self.mock_dispatch.call_args.args[0]

        # Verify that the generated callback is callable and harmless.
        self.assertTrue(callable(dispatched_callback))
        dispatched_callback()

        self.mock_obs.obs_properties_add_button.assert_called_once_with(
            self.mock_props,
            "start_button",
            "Start Timer",
            ("dispatched", dispatched_callback),
        )

    def test_button_without_long_description(self):
        button = MagicMock(name="button")
        self.mock_obs.obs_properties_add_button.return_value = button

        self.factory.button(
            "start",
            "Start Timer",
            long_desc="",
        )

        self.mock_obs.obs_property_set_long_description.assert_not_called()

    def test_button_hidden(self):
        button = MagicMock(name="button")
        callback = MagicMock(name="callback")

        self.mock_obs.obs_properties_add_button.return_value = button

        result = self.factory.button(
            "start",
            "Start Timer",
            callback=callback,
            show=False,
        )

        self.assertIs(result, button)

        self.mock_obs.obs_property_set_visible.assert_called_once_with(
            button,
            False,
        )

    def test_button_long_description_and_hidden(self):
        button = MagicMock(name="button")
        callback = MagicMock(name="callback")

        self.mock_obs.obs_properties_add_button.return_value = button

        self.factory.button(
            "start",
            "Start Timer",
            callback=callback,
            long_desc="Description",
            show=False,
        )

        self.assertEqual(
            self.mock_obs.mock_calls,
            [
                call.obs_properties_add_button(
                    self.mock_props,
                    "start_button",
                    "Start Timer",
                    ("dispatched", callback),
                ),
                call.obs_property_set_long_description(
                    button,
                    "Description",
                ),
                call.obs_property_set_visible(
                    button,
                    False,
                ),
            ],
        )

    def test_int_with_unit(self):
        prop = MagicMock(name="int_property")
        self.mock_obs.obs_properties_add_int.return_value = prop

        callback = MagicMock(name="modified_callback")

        self.factory.int_with_unit(
            "duration",
            "Duration",
            0,
            3600,
            1,
            unit=" seconds",
            modified_callback=callback,
        )

        self.mock_obs.obs_properties_add_int.assert_called_once_with(
            self.mock_props,
            "duration",
            "Duration",
            0,
            3600,
            1,
        )

        self.mock_obs.obs_property_int_set_suffix.assert_called_once_with(
            prop,
            " seconds",
        )

        self.mock_dispatch.assert_called_once_with(callback)

        self.mock_obs.obs_property_set_modified_callback.assert_called_once_with(
            prop,
            ("dispatched", callback),
        )

    def test_int_with_unit_without_unit(self):
        prop = MagicMock(name="int_property")
        self.mock_obs.obs_properties_add_int.return_value = prop

        self.factory.int_with_unit(
            "duration",
            "Duration",
            0,
            3600,
            1,
        )

        self.mock_obs.obs_properties_add_int.assert_called_once_with(
            self.mock_props,
            "duration",
            "Duration",
            0,
            3600,
            1,
        )

        self.mock_obs.obs_property_int_set_suffix.assert_not_called()
        self.mock_obs.obs_property_set_modified_callback.assert_not_called()
        self.mock_dispatch.assert_not_called()

    def test_int_with_unit_without_modified_callback(self):
        prop = MagicMock(name="int_property")
        self.mock_obs.obs_properties_add_int.return_value = prop

        self.factory.int_with_unit(
            "duration",
            "Duration",
            1,
            100,
            5,
            unit=" sec",
            modified_callback=None,
        )

        self.mock_obs.obs_property_int_set_suffix.assert_called_once_with(
            prop,
            " sec",
        )

        self.mock_obs.obs_property_set_modified_callback.assert_not_called()
        self.mock_dispatch.assert_not_called()

    def test_int_with_unit_all_argument_combinations(self):
        test_cases = [
            {
                "name": "seconds",
                "desc": "Seconds",
                "min": 0,
                "max": 60,
                "step": 1,
                "unit": None,
                "callback": None,
            },
            {
                "name": "minutes",
                "desc": "Minutes",
                "min": 1,
                "max": 60,
                "step": 1,
                "unit": " min",
                "callback": None,
            },
            {
                "name": "milliseconds",
                "desc": "Milliseconds",
                "min": 0,
                "max": 10000,
                "step": 100,
                "unit": " ms",
                "callback": MagicMock(name="callback"),
            },
        ]

        for case in test_cases:
            with self.subTest(case=case):
                self.mock_obs.reset_mock()
                self.mock_dispatch.reset_mock()

                prop = MagicMock(name="property")
                self.mock_obs.obs_properties_add_int.return_value = prop

                self.factory.int_with_unit(
                    case["name"],
                    case["desc"],
                    case["min"],
                    case["max"],
                    case["step"],
                    unit=case["unit"],
                    modified_callback=case["callback"],
                )

                self.mock_obs.obs_properties_add_int.assert_called_once_with(
                    self.mock_props,
                    case["name"],
                    case["desc"],
                    case["min"],
                    case["max"],
                    case["step"],
                )

                if case["unit"] is None:
                    self.mock_obs.obs_property_int_set_suffix.assert_not_called()
                else:
                    self.mock_obs.obs_property_int_set_suffix.assert_called_once_with(
                        prop,
                        case["unit"],
                    )

                if case["callback"] is None:
                    self.mock_dispatch.assert_not_called()
                    self.mock_obs.obs_property_set_modified_callback.assert_not_called()
                else:
                    self.mock_dispatch.assert_called_once_with(
                        case["callback"]
                    )
                    self.mock_obs.obs_property_set_modified_callback.assert_called_once_with(
                        prop,
                        ("dispatched", case["callback"]),
                    )

if __name__ == '__main__':
    unittest.main()
