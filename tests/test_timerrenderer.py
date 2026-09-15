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
        self.renderer.set_source('BRB Timer') # TODO: Convert to using SCRIPT_NAME const.
        mock_obs.source_set_text.return_value = True

        result = self.renderer.set_text("01:23")

        self.assertTrue(result)

        mock_obs.source_set_text.assert_called_once_with(self.source_name, "01:23")

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
        mock_obs.source_set_text.return_value = False

        result = self.renderer.set_text("01:23")

        self.assertFalse(result)

        mock_obs.source_set_text.assert_called_once_with(self.source_name, "01:23")
        mock_obs.source_update.assert_not_called()

    @patch("brb_timer.OBS")
    def test_clear(self, mock_obs):
        with patch.object(self.renderer, "set_text") as mock_set_text, \
             patch.object(self.renderer, "hide") as mock_hide:

            self.renderer.clear()

        mock_set_text.assert_called_once_with("")
        mock_hide.assert_called_once_with()

    def test_show(self):
        with patch(
            "brb_timer.OBS.sceneitem_set_visible_by_name",
        ) as mock_set_visibility:

            self.renderer.show()

        mock_set_visibility.assert_called_once_with('BRB Timer', True)

    def test_hide(self):
        with patch(
            "brb_timer.OBS.sceneitem_set_visible_by_name",
        ) as mock_set_visibility:

            self.renderer.hide()

        mock_set_visibility.assert_called_once_with('BRB Timer', False)

if __name__ == '__main__':
    unittest.main()
