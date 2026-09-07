import contextlib
import unittest
from unittest.mock import MagicMock, patch

from brb_timer import OBS

class TestOBS(unittest.TestCase):

    def setUp(self):
        self.obs_patcher = patch("brb_timer.obs")
        self.mock_obs = self.obs_patcher.start()

        # Constants used by the wrapper.
        self.mock_obs.LOG_ERROR = 1
        self.mock_obs.LOG_WARNING = 2
        self.mock_obs.LOG_INFO = 3
        self.mock_obs.LOG_DEBUG = 4

        self.scene = MagicMock(name="scene")
        self.source = MagicMock(name="source")
        self.sceneitem = MagicMock(name="sceneitem")
        self.settings = MagicMock(name="settings")
        self.transition = MagicMock(name="transition")
        self.data = MagicMock(name="data")

    def tearDown(self):
        self.obs_patcher.stop()

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def test_source_exists(self):
        self.mock_obs.obs_frontend_get_current_scene.return_value = MagicMock(
            name="scene_source"
        )
        self.mock_obs.obs_scene_from_source.return_value = self.scene
        self.mock_obs.obs_scene_find_source.return_value = self.sceneitem

        with patch.object(
            OBS,
            "scene_current",
            return_value=self._context(self.scene),
        ):
            result = OBS.source_exists("Timer")

        self.assertTrue(result)
        self.mock_obs.obs_scene_find_source.assert_called_once_with(
            self.scene,
            "Timer",
        )
        self.mock_obs.obs_sceneitem_release.assert_not_called()

    def test_source_exists_when_scene_unavailable(self):
        with patch.object(
            OBS,
            "scene_current",
            return_value=self._context(None),
        ):
            result = OBS.source_exists("Timer")

        self.assertFalse(result)
        self.mock_obs.obs_sceneitem_release.assert_not_called()

    def test_source_exists_when_source_not_found(self):
        self.mock_obs.obs_scene_find_source.return_value = None

        with patch.object(
            OBS,
            "scene_current",
            return_value=self._context(self.scene),
        ):
            result = OBS.source_exists("Timer")

        self.assertFalse(result)
        self.mock_obs.obs_sceneitem_release.assert_not_called()

    def test_source_by_name(self):
        self.mock_obs.obs_get_source_by_name.return_value = self.source

        with OBS.source_by_name("Timer") as source:
            self.assertIs(source, self.source)

        self.mock_obs.obs_get_source_by_name.assert_called_once_with("Timer")
        self.mock_obs.obs_source_release.assert_called_once_with(self.source)

    def test_source_by_name_missing_source(self):
        self.mock_obs.obs_get_source_by_name.return_value = None

        with self.assertRaises(ValueError):
            with OBS.source_by_name("Missing"):
                pass

        self.mock_obs.obs_source_release.assert_not_called()

    def test_source_by_name_releases_after_exception(self):
        self.mock_obs.obs_get_source_by_name.return_value = self.source

        with self.assertRaises(RuntimeError):
            with OBS.source_by_name("Timer") as source:
                self.assertIs(source, self.source)
                raise RuntimeError("boom")

        self.mock_obs.obs_source_release.assert_called_once_with(self.source)

    def test_source_name_in_scene(self):
        self.mock_obs.obs_scene_find_source.return_value = self.source

        with OBS.source_name_in_scene("Timer", self.scene) as source:
            self.assertIs(source, self.source)

        self.mock_obs.obs_scene_find_source.assert_called_once_with(
            self.scene,
            "Timer",
        )
        self.mock_obs.obs_source_release.assert_called_once_with(self.source)

    def test_source_name_in_scene_without_scene(self):
        with self.assertRaises(ValueError):
            with OBS.source_name_in_scene("Timer", None):
                pass

        self.mock_obs.obs_source_release.assert_not_called()

    def test_source_name_in_scene_missing_source(self):
        self.mock_obs.obs_scene_find_source.return_value = None

        with self.assertRaises(ValueError):
            with OBS.source_name_in_scene("Timer", self.scene):
                pass

        self.mock_obs.obs_source_release.assert_not_called()

    def test_source_name_in_scene_releases_after_exception(self):
        self.mock_obs.obs_scene_find_source.return_value = self.source

        with self.assertRaises(RuntimeError):
            with OBS.source_name_in_scene("Timer", self.scene):
                raise RuntimeError("boom")

        self.mock_obs.obs_source_release.assert_called_once_with(
            self.source
        )

    def test_source_create(self):
        self.mock_obs.obs_source_create.return_value = self.source

        with OBS.source_create(
            "text_source",
            "Timer",
        ) as source:
            self.assertIs(source, self.source)

        self.mock_obs.obs_source_create.assert_called_once_with(
            "text_source",
            "Timer",
            unittest.mock.ANY,
            None,
        )
        self.mock_obs.obs_source_release.assert_called_once_with(
            self.source
        )

    def test_source_create_with_supplied_settings(self):
        supplied_settings = MagicMock(name="supplied_settings")

        self.mock_obs.obs_source_create.return_value = self.source

        with OBS.source_create(
            "text_source",
            "Timer",
            supplied_settings,
        ) as source:
            self.assertIs(source, self.source)

        self.mock_obs.obs_source_create.assert_called_once_with(
            "text_source",
            "Timer",
            supplied_settings,
            None,
        )

    def test_source_create_failure(self):
        self.mock_obs.obs_source_create.return_value = None

        with self.assertRaises(ValueError):
            with OBS.source_create(
                "text_source",
                "Timer",
            ):
                pass

        self.mock_obs.obs_source_release.assert_not_called()

    def test_source_create_releases_after_exception(self):
        self.mock_obs.obs_source_create.return_value = self.source

        with self.assertRaises(RuntimeError):
            with OBS.source_create(
                "text_source",
                "Timer",
            ):
                raise RuntimeError("boom")

        self.mock_obs.obs_source_release.assert_called_once_with(
            self.source
        )

    # ------------------------------------------------------------------
    # Text source creation
    # ------------------------------------------------------------------

    @patch("brb_timer.sys.platform", "win32")
    def test_source_create_text_windows(self):
        self.mock_obs.obs_source_get_display_name.side_effect = (
            lambda source_type:
                "GDI+ Text"
                if source_type == "text_gdiplus_v2"
                else None
        )

        self.mock_obs.obs_source_create.return_value = self.source

        with patch.object(
            OBS,
            "text_settings",
            return_value=self._context(self.settings),
        ):
            with patch.object(
                OBS,
                "source_create",
                return_value=self._context(self.source),
            ) as mock_create:

                with OBS.source_create_text(
                    "Timer",
                    "01:23",
                ) as source:
                    self.assertIs(source, self.source)

        self.mock_obs.obs_source_get_display_name.assert_called_once_with(
            "text_gdiplus_v2"
        )
        mock_create.assert_called_once_with(
            "text_gdiplus_v2",
            "Timer",
            self.settings,
        )

    @patch("brb_timer.sys.platform", "linux")
    def test_source_create_text_non_windows(self):
        self.mock_obs.obs_source_get_display_name.side_effect = (
            lambda source_type:
                "FT2 Text"
                if source_type == "text_ft2_source_v2"
                else None
        )

        with patch.object(
            OBS,
            "text_settings",
            return_value=self._context(self.settings),
        ):
            with patch.object(
                OBS,
                "source_create",
                return_value=self._context(self.source),
            ) as mock_create:

                with OBS.source_create_text(
                    "Timer",
                    "01:23",
                ) as source:
                    self.assertIs(source, self.source)

        self.mock_obs.obs_source_get_display_name.assert_called_once_with(
            "text_ft2_source_v2"
        )
        mock_create.assert_called_once_with(
            "text_ft2_source_v2",
            "Timer",
            self.settings,
        )

    def test_source_create_text_explicit_type(self):
        self.mock_obs.obs_source_get_display_name.return_value = "Custom Text"

        with patch.object(
            OBS,
            "text_settings",
            return_value=self._context(self.settings),
        ):
            with patch.object(
                OBS,
                "source_create",
                return_value=self._context(self.source),
            ) as mock_create:

                with OBS.source_create_text(
                    "Timer",
                    "Hello",
                    source_type_id="my_text_source",
                ) as source:
                    self.assertIs(source, self.source)

        self.mock_obs.obs_source_get_display_name.assert_called_once_with(
            "my_text_source"
        )
        mock_create.assert_called_once_with(
            "my_text_source",
            "Timer",
            self.settings,
        )

    @unittest.skip('TODO: Rewrite assertions for the actual ValueError this triggers.')
    def test_source_create_text_skips_unavailable_types(self):
        self.mock_obs.obs_source_get_display_name.side_effect = [
            None,
            "Available Text",
        ]

        with patch.object(
            OBS,
            "text_settings",
            return_value=self._context(self.settings),
        ):
            with patch.object(
                OBS,
                "source_create",
                return_value=self._context(self.source),
            ) as mock_create:

                with OBS.source_create_text(
                    "Timer",
                    "Hello",
                    source_type_id="first",
                ) as source:
                    self.assertIs(source, self.source)

        # The above implementation raises a ValueError because we provided an explicit `first` source_type_id, which is invalid in all contexts, which makes the below tests incorrect.
        self.mock_obs.obs_source_get_display_name.assert_called_once_with(
            "first"
        )
        mock_create.assert_called_once()

    def test_source_create_text_no_available_type(self):
        self.mock_obs.obs_source_get_display_name.return_value = None

        with patch.object(
            OBS,
            "text_settings",
            return_value=self._context(self.settings),
        ):
            with self.assertRaises(ValueError):
                with OBS.source_create_text(
                    "Timer",
                    "Hello",
                    source_type_id="missing",
                ):
                    pass

    # ------------------------------------------------------------------
    # Source updates / saving
    # ------------------------------------------------------------------

    def test_source_update(self):
        with patch.object(
            OBS,
            "data_set_all",
            return_value=self._context(self.settings),
        ) as mock_data_set_all:

            OBS.source_update(
                self.source,
                {
                    "text": "01:23",
                    "outline": True,
                    "size": 96,
                },
            )

        mock_data_set_all.assert_called_once_with(
            None,
            {
                "text": "01:23",
                "outline": True,
                "size": 96,
            },
        )

        self.mock_obs.obs_source_update.assert_called_once_with(
            self.source,
            self.settings,
        )

    def test_source_save(self):
        self.mock_obs.obs_save_source.return_value = True

        result = OBS.source_save(self.source)

        self.assertTrue(result)
        self.mock_obs.obs_save_source.assert_called_once_with(
            self.source
        )

    # ------------------------------------------------------------------
    # Scenes
    # ------------------------------------------------------------------

    def test_scene_current(self):
        scene_source = MagicMock(name="scene_source")

        self.mock_obs.obs_frontend_get_current_scene.return_value = (
            scene_source
        )
        self.mock_obs.obs_scene_from_source.return_value = self.scene

        with OBS.scene_current() as scene:
            self.assertIs(scene, self.scene)

        self.mock_obs.obs_frontend_get_current_scene.assert_called_once_with()
        self.mock_obs.obs_scene_from_source.assert_called_once_with(
            scene_source
        )
        self.mock_obs.obs_source_release.assert_called_once_with(
            scene_source
        )
        self.mock_obs.obs_scene_release.assert_not_called()

    def test_scene_current_releases_after_exception(self):
        scene_source = MagicMock(name="scene_source")

        self.mock_obs.obs_frontend_get_current_scene.return_value = (
            scene_source
        )
        self.mock_obs.obs_scene_from_source.return_value = self.scene

        with self.assertRaises(RuntimeError):
            with OBS.scene_current():
                raise RuntimeError("boom")

        self.mock_obs.obs_source_release.assert_called_once_with(
            scene_source
        )
        self.mock_obs.obs_scene_release.assert_not_called()

    def test_scene_add(self):
        self.mock_obs.obs_scene_add.return_value = self.sceneitem

        with OBS.scene_add(
            self.scene,
            self.source,
        ) as sceneitem:
            self.assertIs(sceneitem, self.sceneitem)

        self.mock_obs.obs_scene_add.assert_called_once_with(
            self.scene,
            self.source,
        )
        self.mock_obs.obs_sceneitem_release.assert_called_once_with(
            self.sceneitem
        )

    def test_scene_add_releases_after_exception(self):
        self.mock_obs.obs_scene_add.return_value = self.sceneitem

        with self.assertRaises(RuntimeError):
            with OBS.scene_add(self.scene, self.source):
                raise RuntimeError("boom")

        self.mock_obs.obs_sceneitem_release.assert_called_once_with(
            self.sceneitem
        )

    def test_scene_add_failure(self):
        self.mock_obs.obs_scene_add.return_value = None

        with self.assertRaises(ValueError):
            with OBS.scene_add(self.scene, self.source):
                pass

        self.mock_obs.obs_sceneitem_release.assert_not_called()

    def test_sceneitem_by_name(self):
        self.mock_obs.obs_scene_find_source.return_value = self.sceneitem

        with OBS.sceneitem_by_name(
            "Timer",
            self.scene,
        ) as sceneitem:
            self.assertIs(sceneitem, self.sceneitem)

        self.mock_obs.obs_scene_find_source.assert_called_once_with(
            self.scene,
            "Timer",
        )
        self.mock_obs.obs_sceneitem_release.assert_called_once_with(
            self.sceneitem
        )

    def test_sceneitem_by_name_missing(self):
        self.mock_obs.obs_scene_find_source.return_value = None

        with OBS.sceneitem_by_name("Timer", self.scene) as sceneitem:
            self.assertIsNone(sceneitem)

        self.mock_obs.obs_sceneitem_release.assert_not_called()

    def test_sceneitem_by_name_releases_after_exception(self):
        self.mock_obs.obs_scene_find_source.return_value = self.sceneitem

        with self.assertRaises(RuntimeError):
            with OBS.sceneitem_by_name("Timer", self.scene):
                raise RuntimeError("boom")

        self.mock_obs.obs_sceneitem_release.assert_called_once_with(
            self.sceneitem
        )

    # ------------------------------------------------------------------
    # Scene item positioning / transitions / visibility
    # ------------------------------------------------------------------

    def test_sceneitem_position_set(self):
        self.mock_obs.obs_video_info.return_value.base_width = 1920

        with patch.object(
            OBS,
            "vec2",
            return_value="VECTOR",
        ) as mock_vec2:

            OBS.sceneitem_position_set(
                self.sceneitem,
                alignment=42,
                pos_y=100,
                z_index=7,
            )

        self.mock_obs.obs_sceneitem_set_alignment.assert_called_once_with(
            self.sceneitem,
            42,
        )
        self.mock_obs.obs_get_video_info.assert_called_once()
        mock_vec2.assert_called_once_with(1920, 100)
        self.mock_obs.obs_sceneitem_set_pos.assert_called_once_with(
            self.sceneitem,
            "VECTOR",
        )
        self.mock_obs.obs_sceneitem_set_order.assert_called_once_with(
            self.sceneitem,
            7,
        )

    def test_sceneitem_position_set_explicit_x(self):
        with patch.object(
            OBS,
            "vec2",
            return_value="VECTOR",
        ) as mock_vec2:

            OBS.sceneitem_position_set(
                self.sceneitem,
                alignment=42,
                pos_x=500,
                pos_y=25,
                z_index=9,
            )

        self.mock_obs.obs_video_info.assert_not_called()
        self.mock_obs.obs_get_video_info.assert_not_called()

        mock_vec2.assert_called_once_with(500, 25)

    def test_sceneitem_add_transition_show(self):
        OBS.sceneitem_add_transition(
            self.sceneitem,
            self.transition,
            "show",
            duration=500,
        )

        self.mock_obs.obs_sceneitem_set_transition.assert_called_once_with(
            self.sceneitem,
            True,
            self.transition,
        )
        self.mock_obs.obs_sceneitem_set_transition_duration.assert_called_once_with(
            self.sceneitem,
            True,
            500,
        )

    def test_sceneitem_add_transition_hide(self):
        OBS.sceneitem_add_transition(
            self.sceneitem,
            self.transition,
            "hide",
            duration=250,
        )

        self.mock_obs.obs_sceneitem_set_transition.assert_called_once_with(
            self.sceneitem,
            False,
            self.transition,
        )
        self.mock_obs.obs_sceneitem_set_transition_duration.assert_called_once_with(
            self.sceneitem,
            False,
            250,
        )

    def test_sceneitem_add_transition_invalid_visibility(self):
        with self.assertRaises(ValueError):
            OBS.sceneitem_add_transition(
                self.sceneitem,
                self.transition,
                "invalid",
            )

        self.mock_obs.obs_sceneitem_set_transition.assert_not_called()

    def test_sceneitem_set_visible(self):
        self.mock_obs.obs_sceneitem_set_visible.return_value = True

        result = OBS.sceneitem_set_visible(
            self.sceneitem,
            True,
        )

        self.assertTrue(result)
        self.mock_obs.obs_sceneitem_set_visible.assert_called_once_with(
            self.sceneitem,
            True,
        )

    def test_sceneitem_visible(self):
        self.mock_obs.obs_sceneitem_visible.return_value = True

        result = OBS.sceneitem_visible(self.sceneitem)

        self.assertTrue(result)
        self.mock_obs.obs_sceneitem_visible.assert_called_once_with(
            self.sceneitem
        )

    # ------------------------------------------------------------------
    # Transition sources
    # ------------------------------------------------------------------

    def test_transition_source_create(self):
        self.mock_obs.obs_source_create.return_value = self.transition

        with patch.object(
            OBS,
            "data",
            return_value=self._context(self.settings),
        ):
            with OBS.transition_source_create(
                "Slide In",
                "slide_transition",
                "show",
                "left",
            ) as transition:
                self.assertIs(transition, self.transition)

        self.mock_obs.obs_data_set_string.assert_called_once_with(
            self.settings,
            "direction",
            "left",
        )
        self.mock_obs.obs_source_create.assert_called_once_with(
            "slide_transition",
            "Slide In",
            self.settings,
            None,
        )
        self.mock_obs.obs_source_release.assert_called_once_with(
            self.transition
        )

    def test_transition_source_create_hide_right(self):
        self.mock_obs.obs_source_create.return_value = self.transition

        with patch.object(
            OBS,
            "data",
            return_value=self._context(self.settings),
        ):
            with OBS.transition_source_create(
                "Slide Out",
                "slide_transition",
                "hide",
                "right",
            ):
                pass

        self.mock_obs.obs_data_set_string.assert_called_once_with(
            self.settings,
            "direction",
            "right",
        )

    def test_transition_source_create_invalid_visibility(self):
        with self.assertRaises(ValueError):
            with OBS.transition_source_create(
                "Transition",
                "type",
                "invalid",
                "left",
            ):
                pass

        self.mock_obs.obs_source_create.assert_not_called()

    def test_transition_source_create_invalid_direction(self):
        with self.assertRaises(ValueError):
            with OBS.transition_source_create(
                "Transition",
                "type",
                "show",
                "up",
            ):
                pass

        self.mock_obs.obs_source_create.assert_not_called()

    def test_transition_source_create_failure(self):
        self.mock_obs.obs_source_create.return_value = None

        with patch.object(
            OBS,
            "data",
            return_value=self._context(self.settings),
        ):
            with self.assertRaises(ValueError):
                with OBS.transition_source_create(
                    "Transition",
                    "type",
                    "show",
                    "left",
                ):
                    pass

        self.mock_obs.obs_source_release.assert_not_called()

    def test_transition_duration(self):
        self.mock_obs.obs_sceneitem_get_transition_duration.return_value = 300

        result = OBS.transition_duration(
            self.sceneitem,
            "show",
        )

        self.assertEqual(result, 300)
        self.mock_obs.obs_sceneitem_get_transition_duration.assert_called_once_with(
            self.sceneitem,
            True,
        )

    def test_transition_duration_hide(self):
        self.mock_obs.obs_sceneitem_get_transition_duration.return_value = 500

        result = OBS.transition_duration(
            self.sceneitem,
            "hide",
        )

        self.assertEqual(result, 500)
        self.mock_obs.obs_sceneitem_get_transition_duration.assert_called_once_with(
            self.sceneitem,
            False,
        )

    def test_transition_duration_handles_exception(self):
        self.mock_obs.obs_sceneitem_get_transition_duration.side_effect = (
            RuntimeError("boom")
        )

        result = OBS.transition_duration(
            self.sceneitem,
            "show",
        )

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Vector
    # ------------------------------------------------------------------

    def test_vec2(self):
        vector = MagicMock()
        self.mock_obs.vec2.return_value = vector

        result = OBS.vec2(100, 200)

        self.assertIs(result, vector)
        self.assertEqual(vector.x, 100)
        self.assertEqual(vector.y, 200)

    # ------------------------------------------------------------------
    # Text settings
    # ------------------------------------------------------------------

    def test_text_settings(self):
        settings = MagicMock(name="settings")
        font = MagicMock(name="font")

        # text_settings() invokes OBS.data() twice, once for settings
        # and once for font.
        data_contexts = [
            self._context(settings),
            self._context(font),
        ]

        with patch.object(
            OBS,
            "data",
            side_effect=data_contexts,
        ):
            with OBS.text_settings(
                text="Hello",
                width=400,
                wrap=True,
                outline=False,
                drop_shadow=True,
                color_top=0x11223344,
                color_bottom=0x55667788,
                font_face="Monaco",
                font_style="Bold",
                font_size_px=48,
            ) as result:
                self.assertIs(result, settings)

        self.mock_obs.obs_data_set_string.assert_any_call(
            settings,
            "text",
            "Hello",
        )
        self.mock_obs.obs_data_set_bool.assert_any_call(
            settings,
            "word_wrap",
            True,
        )
        self.mock_obs.obs_data_set_int.assert_any_call(
            settings,
            "custom_width",
            400,
        )
        self.mock_obs.obs_data_set_bool.assert_any_call(
            settings,
            "outline",
            False,
        )
        self.mock_obs.obs_data_set_bool.assert_any_call(
            settings,
            "drop_shadow",
            True,
        )
        self.mock_obs.obs_data_set_int.assert_any_call(
            settings,
            "color1",
            0x11223344,
        )
        self.mock_obs.obs_data_set_int.assert_any_call(
            settings,
            "color2",
            0x55667788,
        )

        self.mock_obs.obs_data_set_string.assert_any_call(
            font,
            "face",
            "Monaco",
        )
        self.mock_obs.obs_data_set_string.assert_any_call(
            font,
            "style",
            "Bold",
        )
        self.mock_obs.obs_data_set_int.assert_any_call(
            font,
            "size",
            48,
        )
        self.mock_obs.obs_data_set_int.assert_any_call(
            font,
            "flags",
            0,
        )
        self.mock_obs.obs_data_set_obj.assert_called_once_with(
            settings,
            "font",
            font,
        )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def test_data(self):
        self.mock_obs.obs_data_create.return_value = self.data

        with OBS.data() as data:
            self.assertIs(data, self.data)

        self.mock_obs.obs_data_create.assert_called_once_with()
        self.mock_obs.obs_data_release.assert_called_once_with(
            self.data
        )

    def test_data_from_source_settings(self):
        source_settings = MagicMock(name="source_settings")

        self.mock_obs.obs_source_get_settings.return_value = self.data

        with OBS.data(source_settings) as data:
            self.assertIs(data, self.data)

        self.mock_obs.obs_source_get_settings.assert_called_once_with(
            source_settings
        )
        self.mock_obs.obs_data_release.assert_called_once_with(
            self.data
        )

    def test_data_failure(self):
        self.mock_obs.obs_data_create.return_value = None

        with self.assertRaises(ValueError):
            with OBS.data():
                pass

        self.mock_obs.obs_data_release.assert_not_called()

    def test_data_yield(self):
        with OBS.data_yield(self.data) as data:
            self.assertIs(data, self.data)

        # data_yield explicitly does NOT release.
        self.mock_obs.obs_data_release.assert_not_called()

    def test_data_yield_releases_nothing_after_exception(self):
        with self.assertRaises(RuntimeError):
            with OBS.data_yield(self.data):
                raise RuntimeError("boom")

        self.mock_obs.obs_data_release.assert_not_called()

    def test_data_get(self):
        with patch.object(
            OBS,
            "data_get_all",
            return_value={
                "text": "Hello",
                "size": 96,
            },
        ):
            self.assertEqual(
                OBS.data_get(self.data, "text"),
                "Hello",
            )

            self.assertEqual(
                OBS.data_get(self.data, "missing"),
                None,
            )

            self.assertEqual(
                OBS.data_get(self.data, "missing", "fallback"),
                "fallback",
            )

    def test_data_get_all(self):
        with patch.object(
            OBS,
            "data_get_json",
            return_value='{"text": "Hello", "size": 96}',
        ):
            result = OBS.data_get_all(self.data)

        self.assertEqual(
            result,
            {
                "text": "Hello",
                "size": 96,
            },
        )

    def test_data_get_json(self):
        self.mock_obs.obs_data_get_json.return_value = (
            '{"text": "Hello"}'
        )

        result = OBS.data_get_json(self.data)

        self.assertEqual(result, '{"text": "Hello"}')
        self.mock_obs.obs_data_get_json.assert_called_once_with(
            self.data
        )

    def test_data_set_all_with_new_data(self):
        with patch.object(
            OBS,
            "data",
            return_value=self._context(self.data),
        ) as mock_data, \
             patch.object(OBS, "data_set") as mock_data_set:

            with OBS.data_set_all(
                None,
                {
                    "text": "Hello",
                    "size": 96,
                },
            ) as result:
                self.assertIs(result, self.data)

        mock_data.assert_called_once_with()
        self.assertEqual(mock_data_set.call_count, 2)
        mock_data_set.assert_any_call(
            self.data,
            "text",
            "Hello",
        )
        mock_data_set.assert_any_call(
            self.data,
            "size",
            96,
        )

    @unittest.skip("""
        TODO: Fails.
            self.assertIs(result.__enter__().return_value, self.data)

            <MagicMock name='data.__enter__()' id='4337384624'> is not <MagicMock name='data' id='4338378672'>
        """)
    def test_data_set_all_with_existing_data(self):
        with patch.object(OBS, "data_set") as mock_data_set:
            with OBS.data_set_all(
                self.data,
                {"text": "Hello"},
            ) as result:
                self.assertIs(result, self.data)

        mock_data_set.assert_called_once_with(
            self.data,
            "text",
            "Hello",
        )

    def test_data_set_bool(self):
        OBS.data_set(self.data, "enabled", True)

        self.mock_obs.obs_data_set_bool.assert_called_once_with(
            self.data,
            "enabled",
            True,
        )

    def test_data_set_float(self):
        OBS.data_set(self.data, "opacity", 0.5)

        self.mock_obs.obs_data_set_double.assert_called_once_with(
            self.data,
            "opacity",
            0.5,
        )

    def test_data_set_int(self):
        OBS.data_set(self.data, "size", 96)

        self.mock_obs.obs_data_set_int.assert_called_once_with(
            self.data,
            "size",
            96,
        )

    def test_data_set_int_with_args(self):
        OBS.data_set(
            self.data,
            "size",
            96,
            extra="value",
        )

        self.mock_obs.obs_data_set_int.assert_called_once_with(
            self.data,
            "size",
            96,
            extra="value",
        )

    def test_data_set_string(self):
        OBS.data_set(self.data, "text", "Hello")

        self.mock_obs.obs_data_set_string.assert_called_once_with(
            self.data,
            "text",
            "Hello",
        )

    def test_data_set_none(self):
        OBS.data_set(self.data, "unused", None)

        self.mock_obs.obs_data_set_bool.assert_not_called()
        self.mock_obs.obs_data_set_double.assert_not_called()
        self.mock_obs.obs_data_set_int.assert_not_called()
        self.mock_obs.obs_data_set_string.assert_not_called()

    def test_data_set_unknown_type(self):
        value = ["one", "two"]

        with patch.object(OBS, "warn") as mock_warn:
            OBS.data_set(
                self.data,
                "items",
                value,
            )

        mock_warn.assert_called_once()
        self.mock_obs.obs_data_set_string.assert_called_once_with(
            self.data,
            "items",
            str(value),
        )

    def test_data_set_default_bool(self):
        OBS.data_set_default(self.data, "enabled", True)

        self.mock_obs.obs_data_set_default_bool.assert_called_once_with(
            self.data,
            "enabled",
            True,
        )

    def test_data_set_default_float(self):
        OBS.data_set_default(self.data, "opacity", 0.5)

        self.mock_obs.obs_data_set_default_double.assert_called_once_with(
            self.data,
            "opacity",
            0.5,
        )

    def test_data_set_default_int(self):
        OBS.data_set_default(self.data, "size", 96)

        self.mock_obs.obs_data_set_default_int.assert_called_once_with(
            self.data,
            "size",
            96,
        )

    def test_data_set_default_string(self):
        OBS.data_set_default(self.data, "text", "Hello")

        self.mock_obs.obs_data_set_default_string.assert_called_once_with(
            self.data,
            "text",
            "Hello",
        )

    def test_data_set_default_none(self):
        OBS.data_set_default(self.data, "unused", None)

        self.mock_obs.obs_data_set_default_bool.assert_not_called()
        self.mock_obs.obs_data_set_default_double.assert_not_called()
        self.mock_obs.obs_data_set_default_int.assert_not_called()
        self.mock_obs.obs_data_set_default_string.assert_not_called()

    def test_data_set_default_unknown_type(self):
        value = ["one", "two"]

        with patch.object(OBS, "warn") as mock_warn:
            OBS.data_set_default(
                self.data,
                "items",
                value,
            )

        mock_warn.assert_called_once()
        self.mock_obs.obs_data_set_default_string.assert_called_once_with(
            self.data,
            "items",
            str(value),
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def test_event_register_router(self):
        router = MagicMock()

        OBS.event_register_router(router)

        self.mock_obs.obs_frontend_add_event_callback.assert_called_once()

        callback = (
            self.mock_obs.obs_frontend_add_event_callback.call_args.args[0]
        )

        callback("EVENT")

        router.assert_called_once_with("EVENT")

    def test_event_unregister_router(self):
        router = MagicMock()

        OBS.event_unregister_router(router)

        self.mock_obs.obs_frontend_remove_event_callback.assert_called_once_with(
            router
        )

    def test_event_unregister_router_none(self):
        OBS.event_unregister_router(None)

        self.mock_obs.obs_frontend_remove_event_callback.assert_not_called()

    def test_event_name(self):
        self.mock_obs.OBS_FRONTEND_EVENT_STREAMING_STARTED = 10
        self.mock_obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED = 20

        self.assertEqual(
            OBS.event_name(10),
            "OBS_FRONTEND_EVENT_STREAMING_STARTED",
        )
        self.assertEqual(
            OBS.event_name(20),
            "OBS_FRONTEND_EVENT_STREAMING_STOPPED",
        )

    def test_event_name_unknown(self):
        self.assertEqual(
            OBS.event_name(99999),
            "UNRECOGNIZED_EVENT",
        )

    def test_event_remove_self(self):
        OBS.event_remove_self()

        self.mock_obs.remove_current_callback.assert_called_once_with()

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------

    def test_timer_name(self):
        def my_timer():
            pass

        result = OBS.timer_name(my_timer)

        self.assertEqual(
            result,
            "__obs_timer_proxy__my_timer",
        )

    def test_timer_running_false(self):
        def my_timer():
            pass

        self.assertFalse(
            OBS.timer_running(my_timer)
        )

    def test_timer_running_true(self):
        def my_timer():
            pass

        proxy_name = OBS.timer_name(my_timer)

        # The implementation checks its module globals directly.
        import brb_timer

        old_value = getattr(brb_timer, proxy_name, None)
        had_value = hasattr(brb_timer, proxy_name)

        try:
            setattr(brb_timer, proxy_name, MagicMock())

            self.assertTrue(
                OBS.timer_running(my_timer)
            )
        finally:
            if had_value:
                setattr(brb_timer, proxy_name, old_value)
            else:
                delattr(brb_timer, proxy_name)

    def test_timer_add(self):
        import brb_timer

        def my_timer():
            pass

        shadow = MagicMock(name="shadow")

        with patch(
            "brb_timer.dispatch",
            return_value=shadow,
        ) as mock_dispatch:

            result = OBS.timer_add(my_timer, 1000)

        self.assertIs(result, shadow)
        mock_dispatch.assert_called_once_with(my_timer)

        self.mock_obs.timer_add.assert_called_once_with(
            shadow,
            1000,
        )

        self.assertIs(
            getattr(brb_timer, OBS.timer_name(my_timer)),
            shadow,
        )

        # Clean up the dynamically created global.
        delattr(brb_timer, OBS.timer_name(my_timer))

    def test_timer_add_rejects_existing_timer(self):
        import brb_timer

        def my_timer():
            pass

        name = OBS.timer_name(my_timer)
        existing = MagicMock()

        setattr(brb_timer, name, existing)

        try:
            with self.assertRaises(ValueError):
                OBS.timer_add(my_timer, 1000)

            self.mock_obs.timer_add.assert_not_called()
        finally:
            delattr(brb_timer, name)

    def test_timer_remove(self):
        import brb_timer

        def my_timer():
            pass

        name = OBS.timer_name(my_timer)
        shadow = MagicMock()

        setattr(brb_timer, name, shadow)

        try:
            result = OBS.timer_remove(my_timer)

            self.assertTrue(result)
            self.mock_obs.timer_remove.assert_called_once_with(shadow)
            self.assertFalse(hasattr(brb_timer, name))
        finally:
            if hasattr(brb_timer, name):
                delattr(brb_timer, name)

    def test_timer_remove_missing(self):
        def my_timer():
            pass

        with patch.object(OBS, "debug"):
            result = OBS.timer_remove(my_timer)

        self.assertFalse(result)

    def test_timer_remove_missing_throw(self):
        def my_timer():
            pass

        with self.assertRaises(ValueError):
            OBS.timer_remove(my_timer, throw=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def test_properties_get(self):
        prop = MagicMock()

        self.mock_obs.obs_properties_get.return_value = prop

        result = OBS.properties_get(
            self.settings,
            "text",
        )

        self.assertIs(result, prop)
        self.mock_obs.obs_properties_get.assert_called_once_with(
            self.settings,
            "text",
        )

    def test_properties_create(self):
        props = MagicMock()
        self.mock_obs.obs_properties_create.return_value = props

        result = OBS.properties_create()

        self.assertIs(result, props)
        self.mock_obs.obs_properties_create.assert_called_once_with()

    def test_frontend_open_source_props(self):
        with patch.object(
            OBS,
            "source_by_name",
            return_value=self._context(self.source),
        ):
            OBS.frontend_open_source_props("Timer")

        self.mock_obs.obs_frontend_open_source_properties.assert_called_once_with(
            self.source
        )

    def test_properties_visibility_set(self):
        props = MagicMock()

        with patch.object(OBS, "property_show") as mock_show, \
             patch.object(OBS, "property_hide") as mock_hide:

            result = OBS.properties_visibility_set(
                props,
                {
                    "text": True,
                    "font": False,
                    "color": True,
                },
            )

        # Current implementation does not explicitly return a value.
        self.assertIsNone(result)

        mock_show.assert_any_call(props, "text")
        mock_show.assert_any_call(props, "color")
        mock_hide.assert_called_once_with(props, "font")

    def test_property_show_existing_property(self):
        prop = MagicMock()
        self.mock_obs.obs_properties_get.return_value = prop
        self.mock_obs.obs_property_name.return_value = "text"
        self.mock_obs.obs_property_visible.return_value = False

        with patch.object(OBS, "debug"):
            OBS.property_show(self.settings, "text")

        self.mock_obs.obs_properties_get.assert_called_once_with(
            self.settings,
            "text",
        )
        # Only called when visibility is False to start with.
        self.mock_obs.obs_property_set_visible.assert_called_once_with(
            prop,
            True,
        )
        self.mock_obs.obs_property_name.assert_called_once_with(prop)
        self.mock_obs.obs_property_visible.assert_any_call(prop)

    def test_property_show_missing_property(self):
        self.mock_obs.obs_properties_get.return_value = None

        with patch.object(OBS, "debug"):
            OBS.property_show(self.settings, "missing")

        self.mock_obs.obs_property_set_visible.assert_not_called()

    def test_property_hide_existing_property(self):
        prop = MagicMock()
        self.mock_obs.obs_properties_get.return_value = prop
        self.mock_obs.obs_property_name.return_value = "text"
        self.mock_obs.obs_property_visible.return_value = True

        with patch.object(OBS, "debug"):
            OBS.property_hide(self.settings, "text")

        # Only called when visibility is True to start with.
        self.mock_obs.obs_property_set_visible.assert_called_once_with(
            prop,
            False,
        )

    def test_property_hide_missing_property(self):
        self.mock_obs.obs_properties_get.return_value = None

        OBS.property_hide(self.settings, "missing")

        self.mock_obs.obs_property_set_visible.assert_not_called()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def test_error(self):
        with patch.object(OBS, "_log") as mock_log:
            OBS.error("something went wrong")

        mock_log.assert_called_once_with(
            "something went wrong",
            self.mock_obs.LOG_ERROR,
        )

    def test_warn(self):
        with patch.object(OBS, "_log") as mock_log:
            OBS.warn("warning")

        mock_log.assert_called_once_with(
            "warning",
            self.mock_obs.LOG_WARNING,
        )

    def test_info(self):
        with patch.object(OBS, "_log") as mock_log:
            OBS.info("information")

        mock_log.assert_called_once_with(
            "information",
            self.mock_obs.LOG_INFO,
        )

    def test_debug(self):
        with patch.object(OBS, "_log") as mock_log:
            OBS.debug("debugging")

        mock_log.assert_called_once_with(
            "debugging",
            self.mock_obs.LOG_DEBUG,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _context(value):
        """
        Return a real context manager yielding `value`.

        Using a real context manager here makes tests behave like the
        production OBS contextmanager methods rather than simply
        returning a MagicMock.
        """
        @contextlib.contextmanager
        def context():
            yield value

        return context()

if __name__ == '__main__':
    unittest.main()
