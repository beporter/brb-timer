import datetime
import unittest
from unittest.mock import patch

from brb_timer import GuessManager

class TestGuessManager(unittest.TestCase):

    def setUp(self):
        self.manager = GuessManager()
        self.start_time = datetime.datetime(2026, 9, 4, 15, 0, 0)
        self.end_time = datetime.datetime(2026, 9, 4, 15, 1, 23)

    def tearDown(self):
        pass

    def test_clear(self):
        self.manager.active = True
        self.manager.start_time = self.start_time
        self.manager.stop_time = self.end_time
        self.manager.auto_hide_time = self.end_time
        self.manager.guesses = {"alice": 60}
        self.manager.actual_secs = 83
        self.manager._winner = "alice"

        self.manager.clear()

        self.assertFalse(self.manager.active)
        self.assertIsNone(self.manager.start_time)
        self.assertIsNone(self.manager.stop_time)
        self.assertIsNone(self.manager.auto_hide_time)
        self.assertEqual(self.manager.guesses, {})
        self.assertIsNone(self.manager.actual_secs)
        self.assertIsNone(self.manager._winner)

    def test_running(self):
        self.assertFalse(self.manager.running())

        self.manager.start()

        self.assertTrue(self.manager.running())

        self.manager.end(30)

        self.assertFalse(self.manager.running())

    def test_start(self):
        # Start a session.
        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time

            self.manager.start()

        self.assertTrue(self.manager.active)
        self.assertEqual(self.manager.start_time, self.start_time)
        self.assertIsNone(self.manager.stop_time)
        self.assertIsNone(self.manager.auto_hide_time)
        self.assertEqual(self.manager.guesses, {})
        self.assertIsNone(self.manager.actual_secs)
        self.assertIsNone(self.manager._winner)

        # Starting again should reset the session.
        self.manager.guesses = {"alice": 30}

        second_start = self.start_time + datetime.timedelta(seconds=10)

        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = second_start

            self.manager.start()

        self.assertTrue(self.manager.active)
        self.assertEqual(self.manager.start_time, second_start)
        self.assertEqual(self.manager.guesses, {})
        self.assertIsNone(self.manager.stop_time)
        self.assertIsNone(self.manager.auto_hide_time)

    def test_has_guess(self):
        self.manager.start()

        self.assertFalse(self.manager.has_guess("alice"))

        self.assertTrue(self.manager.add_guess("alice", 60))

        self.assertEqual(self.manager.has_guess("alice"), 60)
        self.assertFalse(self.manager.has_guess("bob"))

        # Zero is a valid guess and must not be treated as "no guess".
        self.assertTrue(self.manager.add_guess("bob", 0))
        self.assertEqual(self.manager.has_guess("bob"), 0)

    def test_add_guess(self):
        self.assertFalse(self.manager.add_guess("alice", 60))

        self.manager.start()

        self.assertTrue(self.manager.add_guess("alice", 60))
        self.assertFalse(self.manager.add_guess("alice", 90))

        self.assertEqual(self.manager.guesses, {"alice": 60})

        self.assertTrue(self.manager.add_guess("bob", 90))
        self.assertEqual(
            self.manager.guesses,
            {
                "alice": 60,
                "bob": 90,
            },
        )

    def test_elapsed_time(self):
        # No session has ever been started.
        self.assertIsNone(self.manager.elapsed_time())

        # Active session: elapsed time is calculated against "now".
        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time
            self.manager.start()

            now = self.start_time + datetime.timedelta(seconds=45)
            mock_dt.now.return_value = now

            self.assertEqual(
                self.manager.elapsed_time(),
                datetime.timedelta(seconds=45),
            )

        # Ended session: elapsed time uses the stored stop_time.
        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time
            self.manager.start()

            mock_dt.now.return_value = self.end_time
            self.manager.end(30)

            # elapsed_time() should not depend on the current clock anymore.
            mock_dt.now.return_value = (
                self.end_time + datetime.timedelta(hours=1)
            )

            self.assertEqual(
                self.manager.elapsed_time(),
                datetime.timedelta(seconds=83),
            )

        # A manager that has been manually put into an inactive state
        # without a stop time has no elapsed time.
        self.manager.clear()
        self.manager.active = False
        self.manager.start_time = self.start_time
        self.manager.stop_time = None

        self.assertIsNone(self.manager.elapsed_time())

    def test_timer_str(self):
        # No session.
        self.assertEqual(self.manager.timer_str(), "--:--")

        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time
            mock_dt.duration_str.return_value = 'foo'
            self.manager.start()

            mock_dt.now.return_value = (
                self.start_time + datetime.timedelta(seconds=5)
            )
            self.assertEqual(self.manager.timer_str(), 'foo')
            mock_dt.duration_str.assert_called_with(datetime.timedelta(seconds=5))

            mock_dt.now.return_value = (
                self.start_time + datetime.timedelta(seconds=75)
            )
            self.assertEqual(self.manager.timer_str(), 'foo')
            mock_dt.duration_str.assert_called_with(datetime.timedelta(seconds=75))

            mock_dt.now.return_value = (
                self.start_time + datetime.timedelta(seconds=3665)
            )
            self.assertEqual(self.manager.timer_str(), 'foo')
            mock_dt.duration_str.assert_called_with(datetime.timedelta(seconds=3665))

    def test_end(self):
        # Calling end() when inactive should do nothing.
        self.manager.end(30)

        self.assertFalse(self.manager.active)
        self.assertIsNone(self.manager.stop_time)
        self.assertIsNone(self.manager.auto_hide_time)

        with patch("brb_timer.DT") as mock_dt:
            auto_hide_delay = 30
            mock_dt.now.return_value = self.start_time
            mock_dt.secs_to_delta.return_value = datetime.timedelta(seconds=auto_hide_delay)
            self.manager.start()

            mock_dt.now.return_value = self.end_time
            self.manager.end(auto_hide_delay)

        self.assertFalse(self.manager.active)
        self.assertEqual(self.manager.stop_time, self.end_time)
        self.assertEqual(self.manager.actual_secs, 83)
        self.assertEqual(
            self.manager.auto_hide_time,
            self.end_time + datetime.timedelta(seconds=auto_hide_delay),
        )

        # Ending again should not modify the completed session.
        later = self.end_time + datetime.timedelta(seconds=100)

        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = later
            mock_dt.secs_to_delta.return_value = datetime.timedelta(seconds=15)
            self.manager.end(999)

        self.assertEqual(self.manager.stop_time, self.end_time)
        self.assertEqual(self.manager.actual_secs, 83)
        self.assertEqual(
            self.manager.auto_hide_time,
            self.end_time + datetime.timedelta(seconds=30),
        )

    def test_winner(self):
        # Active session has no winner.
        self.manager.start()
        winner, guess = self.manager.winner()
        self.assertFalse(winner)
        self.assertIsNone(guess)

        # Ended session with no guesses has no winner.
        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time
            self.manager.start()

            mock_dt.now.return_value = self.end_time
            self.manager.end(30)

        winner, guess = self.manager.winner()
        self.assertFalse(winner)
        self.assertIsNone(guess)

        # Guesses over the actual time are disqualified.
        self.manager.guesses = {
            "alice": 100,
            "bob": 120,
        }
        winner, guess = self.manager.winner()
        self.assertFalse(winner)
        self.assertIsNone(guess)

        # Closest guess without going over wins.
        self.manager.guesses = {
            "alice": 30,
            "bob": 70,
            "charlie": 83,
            "dave": 90,
        }

        self.assertEqual(self.manager.winner(), ("charlie", 83))

        # Adding a smaller guess doesn't change the winner.
        self.manager.guesses["eve"] = 50

        self.assertEqual(self.manager.winner(), ("charlie", 83))

        # Exact match wins.
        self.manager.guesses["frank"] = 83

        self.assertEqual(self.manager.winner(), ("frank", 83))

    def test_should_hide(self):
        auto_hide_delay = 30

        # An active BRB should never be hidden.
        self.manager.start()
        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time

            self.assertFalse(self.manager.should_hide())

        # Inactive manager with no auto-hide time should hide.
        self.manager.clear()
        self.assertTrue(self.manager.should_hide())

        with patch("brb_timer.DT") as mock_dt:
            mock_dt.now.return_value = self.start_time
            mock_dt.secs_to_delta.return_value = datetime.timedelta(seconds=auto_hide_delay)
            self.manager.start()

            mock_dt.now.return_value = self.end_time
            self.manager.end(auto_hide_delay)

            hide_time = self.end_time + datetime.timedelta(seconds=auto_hide_delay)

            # Before auto-hide deadline.
            mock_dt.now.return_value = (
                self.end_time + datetime.timedelta(seconds=29)
            )
            self.assertFalse(self.manager.should_hide())

            # Exactly at deadline.
            mock_dt.now.return_value = hide_time
            self.assertTrue(self.manager.should_hide())

            # After deadline.
            mock_dt.now.return_value = (
                hide_time + datetime.timedelta(seconds=1)
            )
            self.assertTrue(self.manager.should_hide())

if __name__ == '__main__':
    unittest.main()
