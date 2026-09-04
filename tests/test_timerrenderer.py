import unittest
from unittest.mock import MagicMock, patch

from brb_timer import TimerRenderer

class TestTimerRenderer(unittest.TestCase):

    def setUp(self):
        self.source_name = "BRB Timer"
        self.renderer = TimerRenderer(self.source_name)

    def tearDown(self):
        pass

    def test_set_source(self):
        new_source = "New Timer Source"

        self.renderer.set_source(new_source)

        self.assertEqual(
            self.renderer.text_source_name,
            new_source,
        )

    @patch("brb_timer.OBS")
    def test_set_text(self, mock_obs):
        source = MagicMock()

        # OBS.source_by_name(...) is used as a context manager.
        mock_obs.source_by_name.return_value.__enter__.return_value = source

        result = self.renderer.set_text("01:23")

        self.assertTrue(result)

        mock_obs.source_by_name.assert_called_once_with(self.source_name)
        mock_obs.source_update.assert_called_once_with(
            source,
            {"text": "01:23"},
        )

    @patch("brb_timer.OBS")
    def test_set_text_without_source_name(self, mock_obs):
        self.renderer.set_source("")

        result = self.renderer.set_text("01:23")

        self.assertFalse(result)
        mock_obs.error.assert_called_once()
        mock_obs.source_by_name.assert_not_called()
        mock_obs.source_update.assert_not_called()

    @patch("brb_timer.OBS")
    def test_set_text_source_not_found(self, mock_obs):
        mock_obs.source_by_name.return_value.__enter__.return_value = None

        result = self.renderer.set_text("01:23")

        self.assertFalse(result)

        mock_obs.source_by_name.assert_called_once_with(self.source_name)
        mock_obs.error.assert_called_once()
        mock_obs.source_update.assert_not_called()

    @patch("brb_timer.OBS")
    def test_clear(self, mock_obs):
        with patch.object(self.renderer, "set_text") as mock_set_text, \
             patch.object(self.renderer, "hide") as mock_hide:

            self.renderer.clear()

        mock_set_text.assert_called_once_with("")
        mock_hide.assert_called_once_with()

    def test_show(self):
        with patch.object(
            self.renderer,
            "_set_visibility",
        ) as mock_set_visibility:

            self.renderer.show()

        mock_set_visibility.assert_called_once_with(True)

    def test_hide(self):
        with patch.object(
            self.renderer,
            "_set_visibility",
        ) as mock_set_visibility:

            self.renderer.hide()

        mock_set_visibility.assert_called_once_with(False)

    @patch("brb_timer.OBS")
    def test_set_visibility(self, mock_obs):
        scene = MagicMock()
        sceneitem = MagicMock()

        # Configure both OBS context managers.
        mock_obs.scene_current.return_value.__enter__.return_value = scene
        mock_obs.sceneitem_by_name.return_value.__enter__.return_value = sceneitem

        self.renderer._set_visibility(True)

        mock_obs.scene_current.assert_called_once_with()
        mock_obs.sceneitem_by_name.assert_called_once_with(
            self.source_name,
            scene,
        )
        mock_obs.sceneitem_set_visible.assert_called_once_with(
            sceneitem,
            True,
        )
        mock_obs.error.assert_not_called()

    @patch("brb_timer.OBS")
    def test_set_visibility_false(self, mock_obs):
        scene = MagicMock()
        sceneitem = MagicMock()

        mock_obs.scene_current.return_value.__enter__.return_value = scene
        mock_obs.sceneitem_by_name.return_value.__enter__.return_value = sceneitem

        self.renderer._set_visibility(False)

        mock_obs.sceneitem_set_visible.assert_called_once_with(
            sceneitem,
            False,
        )

    @patch("brb_timer.OBS")
    def test_set_visibility_handles_exception(self, mock_obs):
        mock_obs.scene_current.side_effect = RuntimeError(
            "OBS connection failed"
        )

        # _set_visibility() catches the exception and should not propagate it.
        self.renderer._set_visibility(True)

        mock_obs.error.assert_called_once_with(
            "Failed to set sceneitem visibility: RuntimeError('OBS connection failed')"
        )

if __name__ == '__main__':
    unittest.main()
