import unittest
from brb_timer import ChatMessage

class TestChatMessage(unittest.TestCase):

    def setUp(self):
        self.message = ChatMessage(
            username='test_user',
            display_name='Test User',
            message='Hello, world!',
            is_mod=True,
            is_broadcaster=False,
        )

    def tearDown(self):
        pass

    def test_dataclass(self):
        self.assertEqual(self.message.username, 'test_user')
        self.assertEqual(self.message.display_name, 'Test User')
        self.assertEqual(self.message.message, 'Hello, world!')
        self.assertTrue(self.message.is_mod)
        self.assertFalse(self.message.is_broadcaster)

        self.assertIsInstance(self.message.username, str)
        self.assertIsInstance(self.message.display_name, str)
        self.assertIsInstance(self.message.message, str)
        self.assertIsInstance(self.message.is_mod, bool)
        self.assertIsInstance(self.message.is_broadcaster, bool)

        with self.assertRaises(TypeError):
            ChatMessage(
                username='test_user',
                display_name='Test User',
                message='Hello',
                is_mod=True,
            )

if __name__ == '__main__':
    unittest.main()
