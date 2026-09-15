import socket
import unittest
from unittest.mock import Mock, patch, PropertyMock

from brb_timer import TwitchIRCClient, ChatMessage

class TestTwitchIRCClient(unittest.TestCase):

    def setUp(self):
        # Capture ALL log messages generated during testing.
        self.obs_patch = patch("brb_timer.OBS")
        self.obs_mock = self.obs_patch.start()

        self.callback = Mock()
        self.client = TwitchIRCClient(
            channel='#TestChannel',
            username='test_user',
            oauth='oauth:test-token',
            callback=self.callback,
        )

    def tearDown(self):
        if self.client is not None:
            self.client.close()

        self.obs_mock.stop()

    def _replace(self, attr_name: str, new_val: any):
        """
        Helper that handles closing resources before swapping in mocks.
        """
        attr = getattr(self.client, attr_name)
        if attr is not None and hasattr(attr, 'close'):
            attr.close()

        setattr(self.client, attr_name, new_val)

    # ------------------------------------------------------------------
    # Construction / lifecycle
    # ------------------------------------------------------------------

    def test_constants(self):
        self.assertEqual(self.client.HOST, "irc.chat.twitch.tv")
        self.assertEqual(self.client.PORT, 6697)
        self.assertEqual(self.client.RECONNECT_DELAY, 5)

    def test_init_normalizes_channel_and_oauth(self):
        client = TwitchIRCClient(
            channel="###MyChannel",
            username="MyUser",
            oauth="oauth:secret-token",
            callback=self.callback,
        )

        try:
            self.assertEqual(client.channel, "mychannel")
            self.assertEqual(client.username, "MyUser")
            self.assertEqual(client.oauth, "secret-token")
            self.assertIs(client.callback, self.callback)

            self.assertIsNone(client.socket)
            self.assertFalse(client.running)
            self.assertIsNone(client.thread)

            self.assertIsNotNone(client.wakeup_reader)
            self.assertIsNotNone(client.wakeup_writer)
        finally:
            client.close()

    def test_init_without_oauth_prefix(self):
        client = TwitchIRCClient(
            channel="channel",
            username="user",
            oauth="secret-token",
            callback=self.callback,
        )

        try:
            self.assertEqual(client.oauth, "secret-token")
        finally:
            client.close()

    def test_start(self):
        mock_thread = Mock()

        with patch(
            "brb_timer.threading.Thread",
            return_value=mock_thread,
        ) as mock_thread_class, \
        patch(
            "brb_timer.SCRIPT_NAME",
            "TestScript",
        ), \
        patch.object(
            self.client,
            "_drain_wakeup",
        ) as mock_drain:

            self.client.start()

        self.assertTrue(self.client.running)
        self.assertIs(self.client.thread, mock_thread)

        mock_drain.assert_called_once_with()

        mock_thread_class.assert_called_once_with(
            target=self.client._receive_loop,
            daemon=True,
            name="TestScript-TwitchIRC",
        )
        mock_thread.start.assert_called_once_with()

    def test_start_when_already_running(self):
        self.client.running = True
        existing_thread = Mock()
        self.client.thread = existing_thread

        with patch(
            "brb_timer.threading.Thread",
        ) as mock_thread_class, \
        patch.object(
            self.client,
            "_drain_wakeup",
        ) as mock_drain:

            self.client.start()

        mock_thread_class.assert_not_called()
        mock_drain.assert_not_called()

        self.assertIs(self.client.thread, existing_thread)

    def test_stop(self):
        thread_mock = Mock()
        thread_mock.is_alive.return_value = True
        socket_mock = Mock()
        writer_mock = Mock()

        self._replace('thread', thread_mock)
        self._replace('socket', socket_mock)
        self._replace('wakeup_writer', writer_mock)

        self.client.stop()

        self.assertFalse(self.client.running)

        writer_mock.send.assert_called_once_with(b'\x00')
        #socket_mock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        #socket_mock.close.assert_called_once_with()
        #thread_mock.is_alive.assert_called_once_with()

    def test_stop_without_socket(self):
        writer_mock = Mock()

        self._replace('wakeup_writer', writer_mock)
        self.client.running = True
        self.client.socket = None
        self.client.thread = None

        self.client.stop()

        self.assertFalse(self.client.running)

        writer_mock.send.assert_called_once_with(
            b"\x00"
        )

        self.obs_mock.debug.assert_not_called()

    def test_stop_handles_wakeup_failure(self):
        writer_mock = Mock()
        writer_mock.send.side_effect = OSError("closed")

        self._replace('wakeup_writer', writer_mock)
        self.client.socket = None
        self.client.thread = None

        self.client.stop()

        self.assertFalse(self.client.running)

    def test_stop_handles_socket_failure(self):
        socket_mock = Mock()
        socket_mock.shutdown.side_effect = OSError("socket failure")

        self.client.socket = socket_mock
        self.client.thread = None

        self.client.stop()

        socket_mock.close.assert_not_called()

    def test_close_closes_wakeup_pair(self):
        reader = self.client.wakeup_reader
        writer = self.client.wakeup_writer

        with patch.object(self.client, "stop") as mock_stop:
            self.client.close()

        mock_stop.assert_called_once_with()

        # The actual socket objects should now be closed.
        with self.assertRaises(OSError):
            reader.getpeername()

        with self.assertRaises(OSError):
            writer.getpeername()

        self.assertIsNone(self.client.wakeup_reader)
        self.assertIsNone(self.client.wakeup_writer)

    # ------------------------------------------------------------------
    # Wakeup mechanism
    # ------------------------------------------------------------------

    def test_create_wakeup_pair(self):
        with patch(
            "brb_timer.socket.socketpair",
            wraps=socket.socketpair,
        ) as mock_socketpair:

            client = TwitchIRCClient(
                "channel",
                "user",
                "token",
                self.callback,
            )

        try:
            mock_socketpair.assert_called_once_with()

            self.assertIsNotNone(client.wakeup_reader)
            self.assertIsNotNone(client.wakeup_writer)
        finally:
            client.close()

    @unittest.skip('TODO: Fails with error that socket.setblocking is read only.')
    def test_drain_wakeup_empty(self):
        # Should return immediately when there is nothing to drain.
        with patch(self.client.wakeup_reader, new_callable=PropertyMock) as mock_setblocking:

        # with patch.object(
        #     self.client.wakeup_reader,
        #     "setblocking",
        #     wraps=self.client.wakeup_reader.setblocking,
        # ) as mock_setblocking:

            self.client._drain_wakeup()

        self.assertEqual(
            mock_setblocking.call_args_list,
            [
                unittest.mock.call(False),
                unittest.mock.call(True),
            ],
        )

    def test_drain_wakeup(self):
        self.client.wakeup_writer.send(b"\x00")

        self.client._drain_wakeup()

        # Verify the socket has actually been drained.
        self.client.wakeup_reader.setblocking(False)

        try:
            with self.assertRaises(BlockingIOError):
                self.client.wakeup_reader.recv(1)
        finally:
            self.client.wakeup_reader.setblocking(True)

    def test_drain_wakeup_multiple_signals(self):
        # Don't fill the socket buffer. Multiple small writes are enough
        # to prove that _drain_wakeup consumes everything currently queued.
        for _ in range(10):
            self.client.wakeup_writer.send(b"\x00")

        self.client._drain_wakeup()

        self.client.wakeup_reader.setblocking(False)

        try:
            with self.assertRaises(BlockingIOError):
                self.client.wakeup_reader.recv(1)
        finally:
            self.client.wakeup_reader.setblocking(True)

    def test_stop_wakes_waiting_thread(self):
        self.client.running = True

        # Simulate the receive thread waiting on select().
        self.client.wakeup_writer.send(b"\x00")

        readable, _, _ = __import__("select").select(
            [self.client.wakeup_reader],
            [],
            [],
            0.1,
        )

        self.assertIn(self.client.wakeup_reader, readable)

        self.client.wakeup_reader.recv(1)

    # ------------------------------------------------------------------
    # IRC connection
    # ------------------------------------------------------------------

    def test_connect(self):
        raw_socket = Mock()
        ssl_socket = Mock()

        with patch(
            "brb_timer.socket.create_connection",
            return_value=raw_socket,
        ) as mock_create_connection, \
        patch(
            "brb_timer.ssl.create_default_context",
        ) as mock_context_factory:

            mock_context = mock_context_factory.return_value
            mock_context.wrap_socket.return_value = ssl_socket

            self.client._connect()

        mock_create_connection.assert_called_once_with(
            ("irc.chat.twitch.tv", 6697),
            timeout=60,
        )

        mock_context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="irc.chat.twitch.tv",
        )

        ssl_socket.setblocking.assert_called_once_with(True)

        self.assertIs(self.client.socket, ssl_socket)

        self.assertEqual(
            ssl_socket.sendall.call_args_list,
            [
                unittest.mock.call(
                    b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
                ),
                unittest.mock.call(
                    b"PASS oauth:test-token\r\n"
                ),
                unittest.mock.call(
                    b"NICK test_user\r\n"
                ),
                unittest.mock.call(
                    b"JOIN #testchannel\r\n"
                ),
            ],
        )

    def test_connect_failure_propagates(self):
        with patch(
            "brb_timer.socket.create_connection",
            side_effect=OSError("connection failed"),
        ), patch(
            "brb_timer.OBS.debug",
        ):

            with self.assertRaises(OSError):
                self.client._connect()

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def test_send_chat(self):
        with patch.object(
            self.client,
            "_send_raw",
        ) as mock_send_raw:

            self.client.send_chat("Hello Twitch!")

        mock_send_raw.assert_called_once_with(
            "PRIVMSG #testchannel :Hello Twitch!"
        )

    def test_send_raw(self):
        socket_mock = Mock()
        self.client.socket = socket_mock

        self.client._send_raw(
            "PRIVMSG #testchannel :Hello!"
        )

        socket_mock.sendall.assert_called_once_with(
            b"PRIVMSG #testchannel :Hello!\r\n"
        )

    @unittest.skip("TODO: The send_lock object returns a contextmanager. Not mocked properly.")
    def test_send_raw_uses_lock(self):
        socket_mock = Mock()
        self.client.socket = socket_mock

        lock_mock = Mock()
        lock_mock.return_value.__enter__.return_value = True
        lock_mock.return_value.__exit__.return_value = True
        self.client.send_lock = lock_mock

        self.client._send_raw("PING")

        lock_mock.return_value.__enter__.assert_called_once()
        lock_mock.return_value.__exit__.assert_called_once()

    # ------------------------------------------------------------------
    # Line handling
    # ------------------------------------------------------------------

    def test_handle_line_ping(self):
        with patch.object(
            self.client,
            "_send_raw",
        ) as mock_send_raw:

            self.client._handle_line(
                "PING :tmi.twitch.tv"
            )

        mock_send_raw.assert_called_once_with(
            "PONG :tmi.twitch.tv"
        )

        self.callback.assert_not_called()

    def test_handle_line_login_failure(self):
        with patch.object(
            self.client,
            "stop",
        ) as mock_stop:

            self.client._handle_line(
                ":tmi.twitch.tv NOTICE * :Login unsuccessful"
            )

        mock_stop.assert_called_once_with()

    def test_handle_line_ignores_non_privmsg(self):
        with patch.object(
            self.client,
            "_parse_privmsg",
        ) as mock_parse:

            self.client._handle_line(
                ":tmi.twitch.tv NOTICE #channel :Hello"
            )

        mock_parse.assert_not_called()
        self.callback.assert_not_called()

    def test_handle_line_privmsg(self):
        message = ChatMessage(
            username="test_user",
            display_name="Test User",
            message="Hello!",
            is_mod=True,
            is_broadcaster=False,
        )

        line = (
            "@mod=1;display-name=Test User "
            ":test_user!test_user@host "
            "PRIVMSG #channel :Hello!"
        )

        with patch.object(
            self.client,
            "_parse_privmsg",
            return_value=message,
        ) as mock_parse:

            self.client._handle_line(line)

        mock_parse.assert_called_once_with(line)
        self.callback.assert_called_once_with(message)

    def test_handle_line_invalid_privmsg(self):
        line = (
            "@mod=1 "
            ":user!user@host "
            "PRIVMSG #channel :Hello!"
        )

        with patch.object(
            self.client,
            "_parse_privmsg",
            return_value=None,
        ) as mock_parse, \
        patch(
            "brb_timer.OBS.debug",
        ):

            self.client._handle_line(line)

        mock_parse.assert_called_once_with(line)
        self.callback.assert_not_called()

    # ------------------------------------------------------------------
    # PRIVMSG parsing
    # ------------------------------------------------------------------

    def test_parse_privmsg(self):
        line = (
            "@badge-info=;badges=moderator/1;"
            "display-name=TestUser;mod=1 "
            ":testuser!testuser@testuser.tmi.twitch.tv "
            "PRIVMSG #testchannel :Hello, Twitch!"
        )

        result = self.client._parse_privmsg(line)

        self.assertIsInstance(result, ChatMessage)
        self.assertEqual(result.username, "testuser")
        self.assertEqual(result.display_name, "TestUser")
        self.assertEqual(result.message, "Hello, Twitch!")
        self.assertTrue(result.is_mod)
        self.assertFalse(result.is_broadcaster)

    def test_parse_privmsg_broadcaster(self):
        line = (
            "@display-name=Streamer;mod=0;"
            "badges=broadcaster/1 "
            ":streamer!streamer@host "
            "PRIVMSG #channel :Hello!"
        )

        result = self.client._parse_privmsg(line)

        self.assertIsInstance(result, ChatMessage)
        self.assertEqual(result.username, "streamer")
        self.assertEqual(result.display_name, "Streamer")
        self.assertEqual(result.message, "Hello!")
        self.assertFalse(result.is_mod)
        self.assertTrue(result.is_broadcaster)

    def test_parse_privmsg_display_name_fallback(self):
        line = (
            "@mod=1;badges= "
            ":testuser!testuser@testuser.tmi.twitch.tv "
            "PRIVMSG #channel :Hello!"
        )

        result = self.client._parse_privmsg(line)

        self.assertIsInstance(result, ChatMessage)
        self.assertEqual(result.username, "testuser")
        self.assertEqual(result.display_name, "testuser")
        self.assertEqual(result.message, "Hello!")
        self.assertTrue(result.is_mod)
        self.assertFalse(result.is_broadcaster)

    def test_parse_privmsg_empty_message(self):
        line = (
            "@mod=0 "
            ":user!user@host "
            "PRIVMSG #channel :"
        )

        result = self.client._parse_privmsg(line)

        self.assertIsInstance(result, ChatMessage)
        self.assertEqual(result.message, "")

    def test_parse_privmsg_invalid(self):
        invalid_lines = [
            "",
            "garbage",
            "PRIVMSG #channel :Hello!",
            "@mod=1 broken",
            "@mod=1 :user!user PRIVMSG",
            "@mod=1 :user!user@host NOTICE #channel :Hello!",
            "@mod=1 user!user@host PRIVMSG #channel :Hello!",
            "@mod=1 :user!user@host PRIVMSG",
            "@mod=1 :user!user@host PRIVMSG #channel",
            "@mod=1 :!user@host PRIVMSG #channel :Hello!",
        ]

        for line in invalid_lines:
            with self.subTest(line=line):
                result = self.client._parse_privmsg(line)
                self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Tag parsing
    # ------------------------------------------------------------------

    def test_parse_tags(self):
        test_cases = [
            ("", {}),
            ("mod=1", {"mod": "1"}),
            (
                "mod=1;subscriber=1",
                {
                    "mod": "1",
                    "subscriber": "1",
                },
            ),
            (
                "display-name=TestUser;badges=moderator/1",
                {
                    "display-name": "TestUser",
                    "badges": "moderator/1",
                },
            ),
            (
                "key=value=with=equals",
                {
                    "key": "value=with=equals",
                },
            ),
            (
                "key=value;malformed;another=value",
                {
                    "key": "value",
                    "another": "value",
                },
            ),
        ]

        for raw, expected in test_cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    TwitchIRCClient._parse_tags(raw),
                    expected,
                )

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    @unittest.skip('TODO: Currently hangs with an open socket.')
    def test_receive_loop_wakeup_stops_connection_cycle(self):
        wakeup_reader = Mock()

        with patch.object(
            self.client,
            '_connect',
        ) as mock_connect, patch(
            'brb_timer.select.select',
            return_value=([self.client.wakeup_reader], [], []),
        ):

            self.client.running = True
            self.client.wakeup_reader = wakeup_reader

            # One wakeup causes the inner loop to break. The outer loop would
            # normally reconnect, so arrange for the next condition check to
            # terminate it.
            wakeup_reader.recv = Mock(
                side_effect=lambda _: (
                    setattr(self.client, 'running', False),
                    b'\x00',
                )[1]
            )

            self.client._receive_loop()

        mock_connect.assert_called_once()
        wakeup_reader.recv.assert_called_once_with(1)

    def test_receive_loop_handles_fragmented_lines(self):
        socket_mock = Mock()

        chunks = [
            (
                b'@display-name=TestUser;mod=1;badges=moderator/1 '
                b':testuser!testuser@host PRIVMSG #channel :Hello'
            ),
            b' Twitch!\r\n@display-name=Other;mod=0;badges= ',
            b':other!other@host PRIVMSG #channel :Second!\r\n',
        ]

        socket_mock.recv.side_effect = chunks

        handled = []

        def handle_line(line):
            handled.append(line)
            if len(handled) == 2:
                self.client.running = False

        with patch.object(
            self.client,
            '_connect',
        ), patch.object(
            self.client,
            '_handle_line',
            side_effect=handle_line,
        ) as mock_handle_line, patch(
            'brb_timer.select.select',
            side_effect=lambda readable, _, __: ([socket_mock], [], []),
        ):

            self.client.running = True
            self.client.socket = socket_mock

            self.client._receive_loop()

        self.assertEqual(
            handled,
            [
                '@display-name=TestUser;mod=1;badges=moderator/1 '
                ':testuser!testuser@host PRIVMSG #channel :Hello Twitch!',
                '@display-name=Other;mod=0;badges= '
                ':other!other@host PRIVMSG #channel :Second!',
            ],
        )

        self.assertEqual(mock_handle_line.call_count, 2)

    @unittest.skip('TODO: This hangs with a socket open, as written.')
    def test_receive_loop_handles_multiple_lines_in_one_recv(self):
        self.client.running = True

        fake_socket = Mock()

        fake_socket.recv.side_effect = [
            (
                b"@mod=1 :user!user@host PRIVMSG #channel :One\r\n"
                b"@mod=0 :user!user@host PRIVMSG #channel :Two\r\n"
            ),
            b"",
        ]

        with patch.object(
            self.client,
            "_connect",
        ), patch.object(
            self.client,
            "_handle_line",
        ) as mock_handle, patch(
            "brb_timer.select.select",
            side_effect=[
                ([fake_socket], [], []),
                ([fake_socket], [], []),
            ],
        ), patch(
            "brb_timer.OBS.debug",
        ):

            self.client.socket = fake_socket
            self.client._receive_loop()

        self.assertEqual(
            mock_handle.call_args_list,
            [
                unittest.mock.call(
                    "@mod=1 :user!user@host PRIVMSG #channel :One"
                ),
                unittest.mock.call(
                    "@mod=0 :user!user@host PRIVMSG #channel :Two"
                ),
            ],
        )

    @unittest.skip('TODO: This hangs with a socket open, as written.')
    def test_receive_loop_handles_wakeup(self):
        self.client.running = True

        fake_socket = Mock()

        with patch.object(
            self.client,
            "_connect",
        ), patch(
            "brb_timer.select.select",
            return_value=(
                [self.client.wakeup_reader],
                [],
                [],
            ),
        ) as mock_select, patch(
            "brb_timer.OBS.debug",
        ):

            self.client.socket = fake_socket
            self.client._receive_loop()

        mock_select.assert_called_once_with(
            [fake_socket, self.client.wakeup_reader],
            [],
            [],
        )

        self.client.wakeup_reader.recv.assert_not_called()

    @unittest.skip('TODO: This hangs with a socket open, as written.')
    def test_receive_loop_socket_not_readable(self):
        self.client.running = True

        fake_socket = Mock()

        # Make select report the wakeup socket? No: neither socket should
        # happen in normal select(), but the implementation explicitly
        # handles this case with "continue".
        with patch.object(
            self.client,
            "_connect",
        ), patch(
            "brb_timer.select.select",
            side_effect=[
                ([], [], []),
                ([fake_socket], [], []),
            ],
        ), patch(
            "brb_timer.OBS.debug",
        ):

            fake_socket.recv.side_effect = [b"",]
            self.client.socket = fake_socket
            self.client._receive_loop()

        fake_socket.recv.assert_called_once_with(4096)

    @unittest.skip('TODO: This hangs with a socket open, as written.')
    def test_receive_loop_socket_eof(self):
        self.client.running = True

        fake_socket = Mock()
        fake_socket.recv.return_value = b""

        with patch.object(
            self.client,
            "_connect",
        ), patch(
            "brb_timer.select.select",
            return_value=([fake_socket], [], []),
        ), patch(
            "brb_timer.OBS.debug",
        ):

            self.client.socket = fake_socket
            self.client._receive_loop()

        fake_socket.recv.assert_called_once_with(4096)
        self.assertIsNone(self.client.socket)

    @unittest.skip('TODO: This hangs with a socket open, as written.')
    def test_receive_loop_unexpected_exception(self):
        self.client.running = True

        fake_socket = Mock()
        fake_socket.recv.side_effect = RuntimeError("boom")

        with patch.object(
            self.client,
            "_connect",
        ), patch(
            "brb_timer.select.select",
            return_value=([fake_socket], [], []),
        ), patch(
            "brb_timer.OBS.debug",
        ), patch(
            "brb_timer.OBS.warn",
        ) as mock_warn:

            self.client.socket = fake_socket
            self.client._receive_loop()

        mock_warn.assert_called_once_with(
            "Twitch IRC Listener: RuntimeError('boom')"
        )

        fake_socket.close.assert_called_once_with()
        self.assertIsNone(self.client.socket)

    def test_receive_loop_exception_does_not_sleep_when_stopped(self):
        self.client.running = False

        with patch.object(
            self.client,
            "_connect",
        ), patch(
            "brb_timer.time.sleep",
        ) as mock_sleep, patch(
            "brb_timer.OBS.debug",
        ):

            self.client._receive_loop()

        mock_sleep.assert_not_called()

    def test_receive_loop_connect_exception_retries_after_delay(self):
        """
        Keep this test bounded by making the first connection fail and then
        changing running=False from the mock. This verifies the reconnect
        path without actually sleeping or looping forever.
        """
        calls = 0

        def connect():
            nonlocal calls
            calls += 1

            if calls == 1:
                raise RuntimeError("connection failed")

            self.client.running = False

        self.client.running = True

        with patch.object(
            self.client,
            "_connect",
            side_effect=connect,
        ), patch(
            "brb_timer.time.sleep",
        ) as mock_sleep, patch(
            "brb_timer.OBS.debug",
        ), patch(
            "brb_timer.OBS.warn",
        ):

            self.client._receive_loop()

        mock_sleep.assert_called_once_with(
            self.client.RECONNECT_DELAY
        )

        self.assertEqual(calls, 2)

    # ------------------------------------------------------------------
    # Regression tests for the logging / parsing problems
    # ------------------------------------------------------------------

    def test_invalid_privmsg_does_not_log_warning(self):
        invalid_lines = [
            "",
            "garbage",
            "@mod=1 broken",
            "@mod=1 :user!user PRIVMSG",
            "@mod=1 :user!user@host PRIVMSG",
            "@mod=1 :user!user@host PRIVMSG #channel",
        ]

        for line in invalid_lines:
            with self.subTest(line=line):
                self.assertIsNone(
                    self.client._parse_privmsg(line)
                )

        # _parse_privmsg should silently reject malformed input.
        self.obs_mock.debug.assert_not_called()
