import unittest
from unittest.mock import MagicMock

from brb_timer import SOURCES, SourceGenerator

class TestSourceGenerator(unittest.TestCase):

    def setUp(self):
        self.obs = MagicMock()

        self.source_name = "Timer Text"
        self.text = "hello world"
        self.text_source_id = "text_ft2_source"

        self.source = MagicMock()
        self.scene = MagicMock()
        self.sceneitem = MagicMock()
        self.show_transition = MagicMock()
        self.hide_transition = MagicMock()

        # Configure the context managers returned by OBS.
        self.source_context = MagicMock()
        self.source_context.__enter__.return_value = self.source

        self.scene_context = MagicMock()
        self.scene_context.__enter__.return_value = self.scene

        self.sceneitem_context = MagicMock()
        self.sceneitem_context.__enter__.return_value = self.sceneitem

        self.show_transition_context = MagicMock()
        self.show_transition_context.__enter__.return_value = (
            self.show_transition
        )

        self.hide_transition_context = MagicMock()
        self.hide_transition_context.__enter__.return_value = (
            self.hide_transition
        )

    def tearDown(self):
        pass

    def _configure_successful_obs(self):
        """Configure OBS mocks for the complete success path."""
        self.obs.source_exists.return_value = False

        self.obs.source_create_text.return_value = (
            self.source_context
        )

        self.obs.scene_current.return_value = (
            self.scene_context
        )

        self.obs.scene_add.return_value = (
            self.sceneitem_context
        )

        self.obs.transition_source_create.side_effect = [
            self.show_transition_context,
            self.hide_transition_context,
        ]

    def test_create_source_exists(self):
        """An existing source should cause an immediate return."""
        self.obs.source_exists.return_value = True

        result = SourceGenerator.create_source(
            self.obs,
            self.source_name,
            self.text,
            self.text_source_id,
        )

        self.assertFalse(result)

        self.obs.source_exists.assert_called_once_with(
            self.source_name,
        )

        self.obs.info.assert_called_once_with(
            f"Source '{self.source_name}' already exists in current scene."
            "Nothing created."
        )

        # Nothing after the early bail-out should have happened.
        self.obs.source_create_text.assert_not_called()
        self.obs.source_save.assert_not_called()
        self.obs.scene_current.assert_not_called()
        self.obs.scene_add.assert_not_called()
        self.obs.transition_source_create.assert_not_called()

    def test_create_source_context(self):
        """
        Verify all OBS resources are entered and released on the normal
        successful path.
        """
        self._configure_successful_obs()

        result = SourceGenerator.create_source(
            self.obs,
            self.source_name,
            self.text,
            self.text_source_id,
        )

        self.assertTrue(result)

        # Source resource.
        self.source_context.__enter__.assert_called_once()
        self.source_context.__exit__.assert_called_once()

        # Scene resource.
        self.scene_context.__enter__.assert_called_once()
        self.scene_context.__exit__.assert_called_once()

        # Scene item resource.
        self.sceneitem_context.__enter__.assert_called_once()
        self.sceneitem_context.__exit__.assert_called_once()

        # Show transition resource.
        self.show_transition_context.__enter__.assert_called_once()
        self.show_transition_context.__exit__.assert_called_once()

        # Hide transition resource.
        self.hide_transition_context.__enter__.assert_called_once()
        self.hide_transition_context.__exit__.assert_called_once()

    def test_create_source_context_scene_missing(self):
        """
        A missing current scene should return False and still release
        both the scene and source resources.
        """
        self.obs.source_exists.return_value = False
        self.obs.source_create_text.return_value = (
            self.source_context
        )

        self.scene_context.__enter__.return_value = None
        self.obs.scene_current.return_value = (
            self.scene_context
        )

        result = SourceGenerator.create_source(
            self.obs,
            self.source_name,
            self.text,
            self.text_source_id,
        )

        self.assertFalse(result)

        self.obs.error.assert_called_once_with(
            "Current frontend source is not an OBS scene."
        )

        self.obs.scene_add.assert_not_called()
        self.obs.transition_source_create.assert_not_called()

        self.source_context.__exit__.assert_called_once()
        self.scene_context.__exit__.assert_called_once()

    def test_create_source_context_sceneitem_missing(self):
        """
        A failed scene insertion should return False and release all
        resources that were entered.
        """
        self.obs.source_exists.return_value = False
        self.obs.source_create_text.return_value = (
            self.source_context
        )
        self.obs.scene_current.return_value = (
            self.scene_context
        )

        self.sceneitem_context.__enter__.return_value = None
        self.obs.scene_add.return_value = (
            self.sceneitem_context
        )

        result = SourceGenerator.create_source(
            self.obs,
            self.source_name,
            self.text,
            self.text_source_id,
        )

        self.assertFalse(result)

        self.obs.error.assert_called_once_with(
            f"Could not add source '{self.source_name}' to current scene.",
        )

        self.obs.transition_source_create.assert_not_called()

        self.source_context.__exit__.assert_called_once()
        self.scene_context.__exit__.assert_called_once()
        self.sceneitem_context.__exit__.assert_called_once()

    def test_create_source_remaining(self):
        """Test the complete successful creation path."""
        self._configure_successful_obs()

        result = SourceGenerator.create_source(
            self.obs,
            self.source_name,
            self.text,
            self.text_source_id,
        )

        self.assertTrue(result)

        # --------------------------------------------------------------
        # Text source creation.

        self.obs.source_exists.assert_called_once_with(
            self.source_name,
        )

        self.obs.source_create_text.assert_called_once_with(
            self.source_name,
            self.text,
            self.text_source_id,
        )

        self.obs.source_save.assert_any_call(
            self.source,
        )

        # --------------------------------------------------------------
        # Scene.

        self.obs.scene_current.assert_called_once_with()

        self.obs.scene_add.assert_called_once_with(
            self.scene,
            self.source,
        )

        # --------------------------------------------------------------
        # Position.

        self.obs.sceneitem_position_set.assert_called_once_with(
            self.sceneitem,
            ((1 << 1) | (1 << 2)),
        )

        # --------------------------------------------------------------
        # Show transition.

        self.obs.transition_source_create.assert_any_call(
            SOURCES.TRANSITION_SHOW,
            SourceGenerator.SHOW_TRANSITION_TYPE,
            "show",
            SourceGenerator.SHOW_TRANSITION_DIR,
        )

        self.obs.source_save.assert_any_call(
            self.show_transition,
        )

        self.obs.sceneitem_add_transition.assert_any_call(
            self.sceneitem,
            self.show_transition,
            "show",
            SourceGenerator.TRANSITION_DURATION_MS,
        )

        # --------------------------------------------------------------
        # Hide transition.

        self.obs.transition_source_create.assert_any_call(
            SOURCES.TRANSITION_HIDE,
            SourceGenerator.HIDE_TRANSITION_TYPE,
            "hide",
            SourceGenerator.HIDE_TRANSITION_DIR,
        )

        self.obs.source_save.assert_any_call(
            self.hide_transition,
        )

        self.obs.sceneitem_add_transition.assert_called_with(
            self.sceneitem,
            self.hide_transition,
            "hide",
            SourceGenerator.TRANSITION_DURATION_MS,
        )

        # Exactly two transitions should have been created.
        self.assertEqual(
            self.obs.transition_source_create.call_count,
            2,
        )

        # Source + show transition + hide transition.
        self.assertEqual(
            self.obs.source_save.call_count,
            3,
        )

    def test_create_source_defaults(self):
        """Verify default text and source ID arguments."""
        self._configure_successful_obs()

        result = SourceGenerator.create_source(
            self.obs,
            self.source_name,
        )

        self.assertTrue(result)

        self.obs.source_create_text.assert_called_once_with(
            self.source_name,
            "hello world",
            None,
        )

    def test_create_source_custom_arguments(self):
        """Verify custom text and platform-specific source ID."""
        self._configure_successful_obs()

        SourceGenerator.create_source(
            self.obs,
            "Custom Timer",
            "00:00",
            "custom-text-source",
        )

        self.obs.source_create_text.assert_called_once_with(
            "Custom Timer",
            "00:00",
            "custom-text-source",
        )

    def test_transition_constants(self):
        """Verify the configuration used by transition creation."""
        self.assertEqual(
            SourceGenerator.SHOW_TRANSITION_TYPE,
            "slide_transition",
        )
        self.assertEqual(
            SourceGenerator.HIDE_TRANSITION_TYPE,
            "slide_transition",
        )
        self.assertEqual(
            SourceGenerator.SHOW_TRANSITION_DIR,
            "left",
        )
        self.assertEqual(
            SourceGenerator.HIDE_TRANSITION_DIR,
            "right",
        )
        self.assertEqual(
            SourceGenerator.TRANSITION_DURATION_MS,
            300,
        )

if __name__ == '__main__':
    unittest.main()
