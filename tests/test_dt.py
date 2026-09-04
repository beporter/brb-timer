import unittest
from brb_timer import DT
import datetime

class TestDT(unittest.TestCase):
    """
    Ref: https://www.calculateme.com/time/seconds/to-hours-minutes-seconds
    """

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_epoch_secs_to_dt(self):
        self.assertEqual(
            datetime.datetime(
                year=1970,
                month=1,
                day=1,
                hour=2,
                minute=26,
                second=5,
                tzinfo=datetime.timezone.utc,
            ),
            DT.epoch_secs_to_dt(8765),
        )

    def test_secs_to_delta(self):
        self.assertEqual(
            datetime.timedelta(
                seconds=5678,
            ),
            DT.secs_to_delta(5678),
        )

    def test_duration_secs_to_dt(self):
        now = datetime.datetime(2000, 1, 1, 12, 0, 0, 345, datetime.timezone.utc)
        with unittest.mock.patch.object(DT, 'now', new=lambda: now):
            expected = datetime.datetime(
                year=2000,
                month=1,
                day=1,
                hour=13,
                minute=34,
                second=38,
                microsecond=345,
                tzinfo=datetime.timezone.utc,
            )

            input = 5678
            self.assertEqual(
                expected,
                DT.duration_secs_to_dt(input),
                'Integer input gets transformed into datetime.',
            )

            self.assertEqual(
                expected,
                DT.duration_secs_to_dt(datetime.timedelta(seconds=input)),
                'timedelta input also becomes datetime.',
            )

    def test_iso_str_to_dt(self):
        expected = datetime.datetime(
            year=2000,
            month=2,
            day=3,
            hour=4,
            minute=5,
            second=6,
            microsecond=346,
            tzinfo=datetime.timezone.utc,
        )
        input = '2000-02-03T04:05:06.000346Z'
        self.assertEqual(
            expected,
            DT.iso_str_to_dt(input),
            'ISO string becomes datetime.',
        )

    def test_now(self):
        self.assertEqual(
            datetime.timezone.utc,
            DT.now().tzinfo,
            'Key attribute of this method is that the timezone must always be utc.',
        )

    def test_epoch(self):
        expected = datetime.datetime(
            year=1970,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=datetime.timezone.utc,
        )
        self.assertEqual(
            expected,
            DT.epoch(),
            'Always returns a unix epoch datetime object.',
        )

    def test_timestamp(self):
        now = datetime.datetime(2000, 1, 1, 12, 0, 0, 347, datetime.timezone.utc)
        with unittest.mock.patch.object(DT, 'now', new=lambda: now):
            expected = 946728000
            self.assertEqual(
                expected,
                DT.timestamp(),
                'Integer value for current datetime.',
            )

    def test_strfdelta(self):
        data = (
            (0, '00 secs'), # TODO: This should probably be `0 secs`
            (10, '10 secs'),
            (100, '01:40'),
            (1000, '16:40'),
            (3600, '01:00:00'),
            (36000, '10:00:00'),
            (99654, '03:40:54'), # TODO: Should be 27 but Deltas increment hours over 24 into days.
        )

        for input, expected in data:
            with self.subTest(secs=input):
                self.assertEqual(
                    expected,
                    DT.strfdelta(datetime.timedelta(seconds=input)),
                )

    def test_strpdelta(self):
        data = (
            ('0', datetime.timedelta(seconds=0)), # TODO: This should probably be `0 secs`
            ('10', datetime.timedelta(seconds=10)),
            ('90', False),
            ('1:40', datetime.timedelta(seconds=100)),
            ('01:50', datetime.timedelta(seconds=110)),
            ('16:40', datetime.timedelta(seconds=1000)),
            ('1:00:00', datetime.timedelta(seconds=3600)),
            ('10:00:00', datetime.timedelta(seconds=36000)),
            ('27:40:54', False), # TODO: Should be 27 but Deltas increment hours over 24 into days.
        )

        for input, expected in data:
            with self.subTest(str=input):
                self.assertEqual(
                    expected,
                    DT.strpdelta(input),
                )

    def test_duration_str(self):
        data = (
            (0, '00 secs'),
            (10.55, '10 secs'),
            (datetime.datetime.fromtimestamp(100), '01:40'),
            (datetime.datetime.fromtimestamp(1000), '16:40'),
            (datetime.timedelta(seconds=3600), '01:00:00'),
            (datetime.timedelta(seconds=36000), '10:00:00'),
        )

        for input, expected in data:
            with self.subTest(secs=input):
                self.assertEqual(
                    expected,
                    DT.duration_str(input),
                )

        with self.assertRaises(ValueError):
            DT.duration_str({'dicts': 'are not a valid input'})

    def test_duration_fmt(self):
        # Tested indirectly via strfdelta.
        pass

if __name__ == '__main__':
    unittest.main()
