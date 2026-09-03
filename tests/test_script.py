import unittest
import brb_timer

class TestScript(unittest.TestCase):
    """
    Naming isn't 100% accurate here. We're testing the
    OBS `script_*()` methods it uses to interact with
    the script.
    """

    def setUp(self):
        pass
        #self.widget = Widget('The widget')

    def tearDown(self):
        pass
        #self.widget.dispose()

    def test_script_description(self):
        desc_str = brb_timer.script_description()
        expected = (
            'BRB Timer',
            'https://github.com/beporter/brb-timer',
            brb_timer.COMMANDS.BRB,
            brb_timer.COMMANDS.AT,
            brb_timer.COMMANDS.BACK,
        )
        for s in expected:
            with self.subTest(str=s):
                self.assertIn(s, desc_str)

    @unittest.skip('TODO: Backfill when possible.')
    def test_script_defaults(self):
        pass

    @unittest.skip('TODO: Backfill when possible.')
    def test_script_properties(self):
        pass

    @unittest.skip('TODO: Backfill when possible.')
    def test_script_load(self):
        pass

    @unittest.skip('TODO: Backfill when possible.')
    def test_script_update(self):
        pass

    @unittest.skip('TODO: Backfill when possible.')
    def test_script_save(self):
        pass

    @unittest.skip('TODO: Backfill when possible.')
    def test_script_unload(self):
        pass

if __name__ == '__main__':
    unittest.main()
