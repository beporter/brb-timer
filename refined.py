"""
BRB Timer for OBS Studio
https://github.com/beporter/brb-timer
"""
from __future__ import annotations
import base64
import contextlib
from dataclasses import dataclass
import datetime
import hashlib
import http
import inspect
import json
import os
import re
import socket
import ssl
import struct
from textwrap import dedent
import threading
import time
from urllib import error, parse, request
from typing import Callable, Dict

import obspython as S

SCRIPT_NAME = "BRB Timer"
SCRIPT_VERSION = 1.0

# =========================================================================
class DEFAULTS:
    AUTO_HIDE_SECS = 120
    TEXT_TIMER = f"{SCRIPT_NAME} - Timer"
    TRANSITION_SHOW = f"{SCRIPT_NAME} - Show Transition"
    TRANSITION_HIDE = f"{SCRIPT_NAME} - Hide Transition"


###########################################################################
# Chat Commands and Messages

# =========================================================================
class COMMANDS:
    BRB = "!brb"
    BACK = "!back"
    AT = "!at"

# =========================================================================
class Message(str):
    def __call__(self, **kwargs):
        return self.format(**kwargs)

# =========================================================================
class MessageMeta(type):
    def __getattribute__(cls, name):
        value = super().__getattribute__(name)
        if isinstance(value, str) and not isinstance(value, Message):
            return Message(value)
        return value

# =========================================================================
class MESSAGES(metaclass=MessageMeta):
    """
    This class cleans up the code that uses these format strings.
    Instead of:

        str.format(MESSAGES.BRB_STARTED, streamer='some user')

    You can use:

        MESSAGES.BRB_STARTED(streamer="some user")
    """
    # Access control.
    ONLY_MOD_START = f"Only mods can start a {COMMANDS.BRB}."
    ONLY_MOD_END = f"Only mods can end a {COMMANDS.BRB} with {COMMANDS.BACK}."

    # Reporting script state.
    ALREADY_RUNNING = (
        f"{COMMANDS.BRB} is already running! "
        f"Must use {COMMANDS.BACK} first."
    )
    NO_BRB = (
        f"There's no {COMMANDS.BRB} running. "
        f"A mod must use {COMMANDS.BRB} first."
    )
    BRB_STARTED = (
        "{streamer} is taking a break! "
        "Guess how long till they're back (without going over!) "
        f"Type `{COMMANDS.AT} MM:SS` in chat!"
    )
    BRB_FINISHED = (
        "{streamer} is back after {time}! "
        "Winner is: {winner}. They were {diff} off."
    )
    BRB_FINISHED_NO_GUESSES = (
        "{streamer} is back after {time}! "
        "But there were no valid "
        f"{COMMANDS.AT} guesses so there's no winner. "
        "(Price is Right rules: closest without going over!)"
    )

    # Guessing.
    BAD_GUESS = (
        "Couldn't understand {user}'s guess. "
        "Format is MINS:SECS. "
        "For example: '1:35:42' means you think the streamer "
        "will return in 1 hour, 35 mins and 42 seconds."
    )
    ALREADY_GUESSED = (
        "{user} already guessed {guess}. "
        "You can't change your guess!"
    )
    GUESS_ACCEPTED = "{user} guesses {guess}."


###########################################################################
# Convience datetime and timedelta methods

# =========================================================================
class DT:
    #----------------------------------------------------------------------
    @classmethod
    def strfdelta(self, delta: int) -> str:
        if delta >= 3600:
            fmt = '%H:%M:%S'
        elif delta >= 60:
            fmt = '%M:%S'
        else:
            fmt = '%S secs'

        return datetime.datetime.fromtimestamp(
            delta,
            tz=datetime.timezone.utc,
        ).strftime(fmt).lstrip('0')

    #----------------------------------------------------------------------
    @classmethod
    def strpdelta(self, time_str: str) -> int | False:
        """
        Parse the provided `[[HH:]MM:]SS` string into a integer number
        of seconds.

        Ref: timedelta https://stackoverflow.com/a/51916936/70876
        Ref: regex https://stackoverflow.com/a/8318367/70876
        """
        pattern = re.compile(
            r'''
                ^(?:
                    (?:
                        (?P<hours>[01]?\d|2[0-3]):
                    )?
                    (?P<minutes>[0-5]?\d):
                )?
                (?P<seconds>[0-5]?\d)$
            ''',
            re.X,
        )
        parts = pattern.match(time_str)
        if parts is None:
            return False

        time_params = {name: float(param) for name, param
                       in parts.groupdict().items() if param}

        return int(datetime.timedelta(**time_params).total_seconds())


###########################################################################
# Twitch Chat Client

# =========================================================================
@dataclass
class ChatMessage(object):
    """
    Message passing container from the IRC client to the command parser.
    """
    username: str
    display_name: str
    message: str
    is_mod: bool
    is_broadcaster: bool

# =========================================================================
type ChatMsgCallback = Callable[[ChatMessage], None]

# =========================================================================
class _WebSocket:
    """
    Minimal RFC6455 WebSocket client implemented with only the
    Python standard library.
    """
    MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    #----------------------------------------------------------------------
    def __init__(self, url: str, timeout: float = 10):
        self.url = url
        self.timeout = timeout
        self.socket: socket.socket | ssl.SSLSocket | None = None

        self._recv_buffer = bytearray()
        self._fragment_buffer = bytearray()
        self._fragment_opcode: int | None = None

    #----------------------------------------------------------------------
    def connect(self) -> None:
        parsed = parse.urlsplit(self.url)
        if parsed.scheme != "wss":
            raise ValueError(
                f"Only wss:// URLs are supported: {self.url!r}"
            )

        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid WebSocket URL: {self.url!r}")

        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        self.socket = ssl.create_default_context().wrap_socket(
            socket.create_connection(
                (host, port),
                timeout=self.timeout,
            ),
            server_hostname=host,
        )

        # RFC 6455 opening handshake.
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        ).encode("ascii")
        self.socket.sendall(request)

        status_line, headers = self._read_http_response()
        try:
            version, status, reason = status_line.split(" ", 2)
        except ValueError as e:
            raise ConnectionError(
                f"Invalid WebSocket HTTP response: {status_line!r}"
            ) from e

        if version != "HTTP/1.1":
            raise ConnectionError(
                f"Unexpected WebSocket HTTP version: {version!r}"
            )

        if status != "101":
            raise ConnectionError(
                f"WebSocket handshake failed: {status} {reason}"
            )

        if headers.get("upgrade", "").lower() != "websocket":
            raise ConnectionError(
                "WebSocket handshake missing Upgrade: websocket"
            )

        connection_tokens = {
            token.strip().lower()
            for token in headers.get("connection", "").split(",")
        }
        if "upgrade" not in connection_tokens:
            raise ConnectionError(
                "WebSocket handshake missing Connection: Upgrade"
            )

        accept = headers.get("sec-websocket-accept")
        if accept is None:
            raise ConnectionError(
                "WebSocket handshake missing Sec-WebSocket-Accept"
            )

        expected_accept = base64.b64encode(
            hashlib.sha1((key + self.MAGIC).encode("ascii")).digest()
        ).decode("ascii")

        if accept.strip() != expected_accept:
            raise ConnectionError(
                "Invalid WebSocket Sec-WebSocket-Accept"
            )

        if "sec-websocket-extensions" in headers:
            raise ConnectionError(
                "WebSocket extensions are not supported"
            )

        self.socket.settimeout(1.0)

    #----------------------------------------------------------------------
    def recv_message(self) -> str | bytes | None:
        """
        Receive the next complete WebSocket message.

        Returns:
            str   for text messages
            bytes for binary messages
            None  for a clean WebSocket close
        """
        while True:
            fin, opcode, payload = self._recv_frame()

            # Continuation frame.
            if opcode == 0x0:
                if self._fragment_opcode is None:
                    raise ConnectionError(
                        "Unexpected WebSocket continuation frame"
                    )

                self._fragment_buffer.extend(payload)
                if not fin:
                    continue

                message_opcode = self._fragment_opcode
                data = bytes(self._fragment_buffer)
                self._fragment_buffer.clear()
                self._fragment_opcode = None
                if message_opcode == 0x1:
                    return data.decode("utf-8")

                if message_opcode == 0x2:
                    return data

                raise ConnectionError(
                    f"Invalid fragmented opcode: {message_opcode}"
                )

            # Text or binary data frame.
            if opcode in (0x1, 0x2):
                if self._fragment_opcode is not None:
                    raise ConnectionError(
                        "New WebSocket data frame received "
                        "during fragmented message"
                    )

                if fin:
                    if opcode == 0x1:
                        return payload.decode("utf-8")

                    return payload

                self._fragment_opcode = opcode
                self._fragment_buffer.clear()
                self._fragment_buffer.extend(payload)
                continue

            # Close.
            if opcode == 0x8:
                if len(payload) == 1:
                    raise ConnectionError(
                        "WebSocket close frame has invalid payload length"
                    )

                self._send_close(payload)
                if len(payload) >= 2:
                    close_code = struct.unpack("!H", payload[:2])[0]
                    raise ConnectionError(
                        f"WebSocket closed by server (code {close_code})"
                    )

                raise ConnectionError("WebSocket closed by server")

            # Ping.
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue

            # Pong.
            if opcode == 0xA:
                continue

            raise ConnectionError(
                f"Unsupported WebSocket opcode: {opcode}"
            )

    #----------------------------------------------------------------------
    def close(self) -> None:
        sock = self.socket
        if sock is None:
            return

        try:
            self._send_close(struct.pack("!H", 1000))
        except (OSError, ValueError):
            pass
        finally:
            self.socket = None

            try:
                sock.close()
            except OSError:
                pass

    #----------------------------------------------------------------------
    def _read_http_response(self) -> tuple[str, dict[str, str]]:
        data = bytearray()

        while b"\r\n\r\n" not in data:
            try:
                chunk = self._recv_socket(4096)
            except socket.timeout:
                continue

            if not chunk:
                raise ConnectionError(
                    "EOF during WebSocket handshake"
                )

            data.extend(chunk)
            if len(data) > 64 * 1024:
                raise ConnectionError(
                    "WebSocket handshake response exceeds 64 KiB"
                )

        header_bytes, remainder = data.split(b"\r\n\r\n", 1)

        # Preserve anything that happened to arrive after the HTTP headers.
        self._recv_buffer.extend(remainder)
        lines = header_bytes.decode("iso-8859-1").split("\r\n")
        if not lines or not lines[0]:
            raise ConnectionError(
                "Empty WebSocket handshake response"
            )

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue

            if ":" not in line:
                raise ConnectionError(
                    f"Malformed WebSocket HTTP header: {line!r}"
                )

            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

        return lines[0], headers

    #----------------------------------------------------------------------
    def _recv_frame(self) -> tuple[bool, int, bytes]:
        first_two = self._recv_exact(2)
        first = first_two[0]
        second = first_two[1]
        fin = bool(first & 0x80)
        if first & 0x70:
            raise ConnectionError(
                "WebSocket frame uses unsupported RSV bits"
            )

        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if masked:
            raise ConnectionError(
                "Server sent a masked WebSocket frame"
            )

        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
            if length & (1 << 63):
                raise ConnectionError(
                    "Invalid WebSocket 64-bit frame length"
                )

        # Control frames must be final and <= 125 bytes.
        if opcode >= 0x8:
            if not fin:
                raise ConnectionError(
                    "Fragmented WebSocket control frame"
                )

            if length > 125:
                raise ConnectionError(
                    "WebSocket control frame exceeds "
                    "125-byte limit"
                )

        # Continuation frames require an active fragmented message.
        if opcode == 0x0 and self._fragment_opcode is None:
            raise ConnectionError(
                "Unexpected WebSocket continuation frame"
            )

        # Reject undefined control opcodes.
        if opcode >= 0x8 and opcode not in (
            0x8,  # Close
            0x9,  # Ping
            0xA,  # Pong
        ):
            raise ConnectionError(
                f"Unknown WebSocket control opcode: {opcode}"
            )

        payload = self._recv_exact(length)
        return fin, opcode, payload

    #----------------------------------------------------------------------
    def _recv_exact(self, size: int) -> bytes:
        while len(self._recv_buffer) < size:
            chunk = self._recv_socket(
                max(4096, size - len(self._recv_buffer))
            )
            if not chunk:
                raise ConnectionError("WebSocket EOF")

            self._recv_buffer.extend(chunk)

        result = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]

        return result

    #----------------------------------------------------------------------
    def _recv_socket(self, size: int) -> bytes:
        if self.socket is None:
            raise ConnectionError("WebSocket is not connected")

        return self.socket.recv(size)

    #----------------------------------------------------------------------
    def _send_close(self, payload: bytes = b"") -> None:
        if len(payload) == 1:
            raise ValueError(
                "WebSocket close payload cannot have length 1"
            )

        if len(payload) > 125:
            raise ValueError(
                "WebSocket close payload exceeds "
                "125-byte control-frame limit"
            )

        try:
            self._send_frame(0x8, payload)
        except OSError:
            pass # The peer may already have closed the TCP connection.

    #----------------------------------------------------------------------
    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        if self.socket is None:
            raise ConnectionError("WebSocket is not connected")

        if opcode >= 0x8 and len(payload) > 125:
            raise ValueError(
                "WebSocket control frame payload "
                "cannot exceed 125 bytes"
            )

        # RFC 6455 requires client-to-server frames to be masked.
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB",  0x80|opcode, 0x80|length)
        elif length <= 0xFFFF:
            header = struct.pack("!BBH", 0x80|opcode, 0x80|126, length)
        else:
            header = struct.pack("!BBQ", 0x80|opcode, 0x80|127, length)

        masked_payload = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )

        self.socket.sendall(header + mask + masked_payload)


# =========================================================================
class TwitchEventPubClient:
    """
    Twitch's EventSub WebSocket is a text-data/server-to-client connection.
    The client only needs to:
      - receive text/binary/continuation frames
      - respond to Ping with Pong
      - send the WebSocket Close frame
    """
    # BRB Timer for Chat by beporter@users.sourceforge.net
    # https://dev.twitch.tv/console/apps/ja5swzyzsr1euwm0e53h1sxqhk553l
    APP_CLIENT_ID = "ja5swzyzsr1euwm0e53h1sxqhk553l"

    KICKOFF_URL: str = 'https://beporter.github.io/brb-timer/start.html'
    ID_URL: str = 'https://id.twitch.tv'
    API_URL: str = 'https://api.twitch.tv'
    EVENTSUB_URL: str = 'wss://eventsub.wss.twitch.tv/ws'

    # https://dev.twitch.tv/docs/api/reference/#send-chat-message
    OAUTH_SCOPES: list[str] = [
        'user:read:chat',
        'user:write:chat',
        'user:bot',
        'channel:bot',
    ]

    RECONNECT_DELAY: int = 5
    CONNECT_TIMEOUT: int = 10
    HTTP_TIMEOUT: int = 10

    #----------------------------------------------------------------------
    def __init__(
        self,
        oauth: str,
        callback: ChatMsgCallback,
    ):
        self.oauth: str = oauth.removeprefix("oauth:")
        self.callback: Callable = callback

        self.socket: socket.socket = None
        self.send_lock: threading.Lock = threading.Lock()
        self.running: bool = False
        self.thread: threading.Thread = None
        self.stop_event: threading.Event = threading.Event()

        self.client_id: str = self.APP_CLIENT_ID
        self.user: str = ''
        self.bot_user_id: int = None # ID of bot connecting to channel.
        self.channel_user_id = None # ID of channel's owner.

        self.keepalive_timeout: int = 10

    #----------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return

        self.running = True
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._receive_loop_thread,
            daemon=True,
            name=f"{SCRIPT_NAME}-TwitchEventSub",
        )
        self.thread.start()

    #----------------------------------------------------------------------
    def close(self) -> None:
        """
        Permanently close the client.

        The receiver thread owns the WebSocket and is responsible for
        closing it.
        """
        self.stop()

        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                OBS.debug(f"Failed joining Twitch EventSub thread.")
                raise

        self.thread = None
        OBS.debug("Twitch EventSub client shut down.")

    #----------------------------------------------------------------------
    def stop(self) -> None:
        self.running = False
        self.stop_event.set()

    #----------------------------------------------------------------------
    def send_chat(self, message: str) -> None:
        """
        Send a chat message through Twitch Helix.

        This is the EventSub/API equivalent of IRC PRIVMSG.

        Ref: https://dev.twitch.tv/docs/api/reference/#send-chat-message
        """
        with self.send_lock:
            if not self.client_id or not self.bot_user_id:
                raise ConnectionError(
                    "Twitch client is not connected"
                )

            if not self.channel_user_id:
                raise ConnectionError(
                    "Twitch channel identity is unavailable"
                )

            resp = self._api_request(
                "helix/chat/messages",
                body={
                    "broadcaster_id": self.channel_user_id, # Broadcaster's channel
                    "sender_id": self.bot_user_id, # Who you're posting as
                    "message": message,
                },
            )
            if not resp:
                raise ConnectionError(
                    "Twitch Send Chat Message failed"
                )

            data = resp.get("data", [])[0]
            if not data:
                raise ConnectionError(
                    f"Twitch returned no chat result."
                )

            if not data.get("is_sent"):
                raise ConnectionError(
                    "Twitch rejected chat message: "
                    f"{data.get('drop_reason')!r}"
                )

    #----------------------------------------------------------------------
    def _receive_loop_thread(self) -> None:
        while self.running:
            try:
                self._connect()
                self._read_loop()

            except ConnectionError as e:
                OBS.debug(f"Connection error: {e!r}")
                pass

            finally:
                self._close_socket()

            if self.running: # Pause if we're looping.
                self._wait_for_reconnect()

    #----------------------------------------------------------------------
    def _connect(self) -> None:
        """
        Create a fresh EventSub WebSocket session.
        """
        if not self._initialize_identity():
            self.running = False
            return

        self.socket = _WebSocket(self.EVENTSUB_URL, timeout=self.CONNECT_TIMEOUT)
        self.socket.connect()

        raw = self.socket.recv_message()
        if raw is None:
            raise ConnectionError("EventSub closed during welcome")

        if not isinstance(raw, str):
            raise ConnectionError("EventSub welcome was not a text frame")

        try:
            welcome = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConnectionError(f"Invalid EventSub welcome: {e}")

        metadata = welcome.get("metadata", {})
        if metadata.get("message_type") != "session_welcome":
            raise ConnectionError(
                "Expected EventSub session_welcome, got "
                f"{metadata.get('message_type')!r}"
            )

        session = (welcome.get("payload", {}).get("session", {}))
        session_id = session.get("id")
        if not session_id:
            raise ConnectionError(
                "EventSub welcome did not contain a session ID"
            )

        self.keepalive_timeout = int(
            session.get("keepalive_timeout_seconds", 10)
        )

        self._create_chat_subscription(session_id)

        OBS.debug(
            "Twitch EventSub connected "
            f"(session={session_id!r})."
        )

    #----------------------------------------------------------------------
    def _initialize_identity(self) -> None:
        """
        Check the passed oauth token to determine if it's still valid,
        and who it belongs to.

        Ref: https://dev.twitch.tv/docs/authentication/validate-tokens#how-to-validate-a-token
        """
        resp = self._api_request('oauth2/validate')
        if not resp:
            OBS.debug("oauth2/validate failed.")
            return False

        if not resp['login']:
            OBS.debug("OAuth token is not attached to a user.")
            return False

        if not set(self.OAUTH_SCOPES).issubset(resp['scopes']):
            missing_scopes = ", ".join(set(self.OAUTH_SCOPES) - set(resp['scopes']))
            OBS.debug(
                "OAuth token is lacking necessary scopes: "
                f"{missing_scopes!s}"
            )
            return False

        self.user = resp.get('login', '')
        self.client_id = resp.get('client_id', self.APP_CLIENT_ID)
        self.expires_in = resp.get('expires_in', None), # int seconds
        self.channel_user_id = resp.get('user_id', '') # Channel is always the streamer's id...
        self.bot_user_id = self.channel_user_id # ...posting as themselves.

        if (
            not self.client_id
            or not self.bot_user_id
            or not self.channel_user_id
        ):
            OBS.debug("Twitch OAuth validation did not return client_id/user_id")
            return False

        return True

    #----------------------------------------------------------------------
    def _create_chat_subscription(
        self,
        session_id: str,
    ) -> None:
        """
        Subscribe the EventSub WebSocket session to chat messages.

        Twitch requires the bot's user ID in the user_id condition.
        """
        body = {
            "type": "channel.chat.message",
            "version": "1",
            "condition": {
                "broadcaster_user_id": self.channel_user_id, # Broadcaster's channel
                "user_id": self.bot_user_id, # Who you're listening as
            },
            "transport": {
                "method": "websocket",
                "session_id": session_id,
            },
        }

        resp = self._api_request('helix/eventsub/subscriptions', body=body)
        if not resp:
            raise ConnectionError(
                "Failed to create Twitch EventSub subscription."
            )

    #----------------------------------------------------------------------
    def _read_loop(self) -> None:
        if self.socket is None:
            raise ConnectionError("EventSub WebSocket is not connected")

        while self.running:
            try:
                raw = self.socket.recv_message()

            except socket.timeout:
                # The 1-second socket timeout exists solely to make stop()
                # responsive. It is not a connection failure.
                if not self.running:
                    return

                continue

            if not self.running:
                return # Ends thread excution.

            if not isinstance(raw, str):
                continue

            try:
                message = json.loads(raw)
            except json.JSONDecodeError as e:
                OBS.debug(f"Ignoring invalid EventSub JSON: {e!r}")
                continue

            message_type = message.get("metadata", {}).get("message_type")
            if message_type == "notification":
                self._handle_notification(message)
            elif message_type == "session_keepalive":
                continue
            elif message_type == "session_reconnect":
                self._handle_reconnect(message)
                return
            elif message_type == "revocation":
                self._handle_revocation(message)
                return

    #----------------------------------------------------------------------
    def _handle_notification(self, message: dict) -> None:
        payload = message.get('payload', {})
        subscription = payload.get('subscription', {})
        if subscription.get('type') != 'channel.chat.message':
            return

        chat_message = self._parse_eventsub_message(payload.get('event', {}))
        if chat_message is not None:
            self.callback(chat_message)

    #----------------------------------------------------------------------
    def _parse_eventsub_message(self, event: dict) -> ChatMessage | None:
        username = event.get('chatter_user_login', '')
        text = event.get('message', {}).get('text')
        badges = event.get('badges', [])
        if not username or text is None:
            return None

        return ChatMessage(
            username,
            event.get('chatter_user_name', username),
            text,
            any(
                badge.get('set_id') == 'moderator'
                and badge.get('id') == '1'
                for badge in badges
            ),
            any(
                badge.get('set_id') == 'broadcaster'
                and badge.get('id') == '1'
                for badge in badges
            ),
        )

    #----------------------------------------------------------------------
    def _handle_reconnect(self, message: dict) -> None:
        """
        Twitch specifically requires us to keep the old connection alive
        until the new connection has delivered its welcome message.
        """
        session = message.get('payload', {}).get('session', {})
        reconnect_url = session.get('reconnect_url')
        if not reconnect_url:
            raise ConnectionError(
                "EventSub reconnect message has no reconnect URL"
            )

        OBS.debug("Twitch requested EventSub reconnect.")

        old_ws = self.socket
        new_ws = _WebSocket(reconnect_url, timeout=self.CONNECT_TIMEOUT)
        try:
            new_ws.connect()
            raw = new_ws.recv_message()
            if not isinstance(raw, str):
                raise ConnectionError(
                    "EventSub reconnect did not return "
                    "a text welcome message"
                )

            welcome = json.loads(raw)
            message_type = welcome.get('metadata', {}).get('message_type')
            if (message_type != 'session_welcome'):
                raise ConnectionError(
                    "Invalid EventSub reconnect welcome"
                )

            new_session = welcome.get('payload', {}).get('session', {})
            self.keepalive_timeout = int(
                new_session.get(
                    "keepalive_timeout_seconds",
                    self.keepalive_timeout,
                )
            )

            # Only switch after the new socket has successfully completed
            # the WebSocket handshake AND received its EventSub welcome.
            self.socket = new_ws

            # Now that the new connection has inherited the subscriptions,
            # the old socket can be closed.
            if old_ws is not None:
                old_ws.close()

            OBS.debug("Twitch EventSub reconnect completed.")

        except (
            OSError,
            BlockingIOError,
            ConnectionError,
            TimeoutError,
        ) as e:
            OBS.debug(f"reconnect failure: {e!r}")
            new_ws.close()

    #----------------------------------------------------------------------
    def _handle_revocation(self, message: dict) -> None:
        subscription = (message.get("payload", {}).get("subscription", {}))

        OBS.debug(
            "Twitch EventSub subscription revoked: "
            f"type={subscription.get('type')!r}, "
            f"status={subscription.get('status')!r}"
        )

        # Don't reconnect after a revoked authorization.
        self.running = False
        self.stop_event.set()

    #----------------------------------------------------------------------
    def _wait_for_reconnect(self) -> None:
        self.stop_event.wait(self.RECONNECT_DELAY)

    #----------------------------------------------------------------------
    def _close_socket(self) -> None:
        """
        Close the network connection. Called by the receiver thread.
        """
        ws = self.socket
        self.socket = None
        if ws is not None:
            try:
                ws.close()
            except OSError:
                pass

    #----------------------------------------------------------------------
    def _api_request(
        self,
        path: str,
        body: dict|None = None,
        params: dict[str, str|int] = {},
        extra_headers: dict[str, str|int] = {},
    ) -> object|False:
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            extra_headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self._server(path)}/{path}?" + parse.urlencode(params),
            method='POST' if body is not None else 'GET',
            headers=self._headers(extra_headers),
            data=data,
        )

        try:
            with request.urlopen(req, timeout=self.HTTP_TIMEOUT) as r:
                return json.load(r)

        except json.JSONDecodeError:
            detail = f"Invalid JSON payload: {r!r}"
        except UnicodeDecodeError:
            detail = f"Non-unicode payload: {r!r}"
        except error.HTTPError as e:
            match e.code:
                case http.HTTPStatus.UNAUTHORIZED:
                    try:
                        detail = json.load(e.fp)
                    except json.JSONDecodeError:
                        detail = "Unauthorized. (Invalid JSON payload.)"
                    except UnicodeDecodeError:
                        detail = "Unauthorized. (Non-unicode payload.)"

                # Ref: https://dev.twitch.tv/docs/api/guide/#twitch-rate-limits
                case http.HTTPStatus.TOO_MANY_REQUESTS:
                    total = e.headers.get('Ratelimit-Limit')
                    remaining = e.headers.get('Ratelimit-Remaining')
                    reset = e.headers.get('Ratelimit-Reset')
                    detail = (
                        f"Request for {e.url} was rate limited. "
                        f"({remaining}/{total} requests remaining. "
                        f"Reset in {reset!s} secs.)"
                    )

                case _:
                    detail = e.reason

        OBS.debug(f"_api_request failure: {detail}")
        return False

    #----------------------------------------------------------------------
    def _server(self, path: str) -> str:
        match path.split('/', 2)[0]:
            case 'oauth2':
                return self.ID_URL
            case _:
                return self.API_URL

    #----------------------------------------------------------------------
    def _headers(
        self,
        extra: dict[str, str|int] = {},
    ) -> dict[str, str|int]:
        if (
            'client-id' not in map(str.lower, extra.keys())
            and len(self.client_id) > 0
        ):
            extra['Client-ID'] = self.client_id

        return {
            'User-Agent': f"{SCRIPT_NAME} v{SCRIPT_VERSION}",
            'Accept': 'application/json',
            'Authorization': f"Bearer {self.oauth}",
            **extra,
        }


###########################################################################
# Events class

# =========================================================================
class Events:
    source_name: str = DEFAULTS.TEXT_TIMER
    running: bool = False
    start_time: int = 0
    stop_time: int = 0
    auto_hide_secs: int = DEFAULTS.AUTO_HIDE_SECS
    hide_time: int = 0
    guesses: dict[str, int] = {}
    token: str = ''

    #----------------------------------------------------------------------
    def on_event(self, event):
        if event != S.OBS_FRONTEND_EVENT_FINISHED_LOADING:
            return

        if not OBS.source_exists(self.source_name):
            OBS.debug(f"TODO: create source {self.source_name} here")
            #self.create_source(self.source_name, '--:--')

        # Make sure the timer is hidden on startup.
        OBS.sceneitem_set_visible_by_name(self.source_name, False)
        OBS.debug(f"on_event complete")

    #----------------------------------------------------------------------
    def on_chat(self, message: ChatMessage) -> None:
        """
        Invoked by the IRC client whenever a chat message is received.

        This router is only responsible for determining whether to
        respond to an event and routing it to a handler, or ignore it.
        """
        match message.message.strip().split()[0]:
            case COMMANDS.BRB:
                self.command_brb(message)
            case COMMANDS.AT:
                self.command_at(message)
            case COMMANDS.BACK:
                self.command_back(message)
            case _:
                OBS.debug('No BRB commands matched. Skipping.')

    #----------------------------------------------------------------------
    def ticker(self):
        """
        Updates the text source contents. Scheduled as a per-second
        timer. Can only access running python state, not OBS settings or
        props for this script. Can NOT be scheduled via obs.timer_add()
        from a separate python thread (such as the irc client).
        """
        if not self.running:
            return

        if self.hide_time > 0 and int(time.time()) > self.hide_time:
            OBS.debug('hide_time reached, hiding on-screen text')
            OBS.sceneitem_set_visible_by_name(self.source_name, False)
            return

        OBS.source_set_text_by_name(self.source_name, self.ticker_text())

    #----------------------------------------------------------------------
    def ticker_text(self) -> str:
        if self.running:
            diff_secs: int = int(time.time()) - self.start_time
        else:
            diff_secs: int = self.stop_time - self.start_time

        dt: datetime.datetime = datetime.datetime.fromtimestamp(
            diff_secs,
            datetime.timezone.utc,
        )
        return dt.strftime('%M:%S')

    #----------------------------------------------------------------------
    def guess_start(self) -> None:
        if self.running:
            return

        if self.source_name:
            self.running = True
            self.start_time = int(time.time())
            self.stop_time = 0
            self.hide_time = 0

            # (Timer is already running, so just show it.)
            OBS.sceneitem_set_visible_by_name(self.source_name, True)

    #----------------------------------------------------------------------
    def has_guess(self, username: str) -> int | False:
        if username in self.guesses:
            return self.guesses[username]

        return False

    #----------------------------------------------------------------------
    def add_guess(self, username: str, seconds: int) -> bool:
        if not self.running:
            return False # Can't guess when brb isn't running.

        if self.has_guess(username) is not False:
            return False # User already has a guess registered.

        self.guesses[username] = seconds
        return True # Guess added.

    #----------------------------------------------------------------------
    def guess_end(self, auto_hide_secs: int) -> None:
        # Handle a stream ending with no !brb's having been run.
        if not self.running:
            return

        # Otherwise shut down cleanly.
        self.running = False
        self.stop_time = int(time.time())
        self.hide_time = self.stop_time + auto_hide_secs

    #----------------------------------------------------------------------
    def guess_winner(self) -> tuple[str, int] | tuple[False, None]:
        """
        Returns false if no !brb has run, or is still running, or if
        nobody registered any guesses.

        Returns a tuple of (username, guessed_secs) when a winner is
        present.
        """
        if self.running or len(self.guesses) == 0:
            return (False, None) # No winner when brb is still active or nobody guessed.

        qualified = self._qualified_guesses()
        if not qualified:
            return (False, None)

        winner_username, guessed_secs = next(reversed(qualified.items()))

        return (winner_username, guessed_secs)

    #----------------------------------------------------------------------
    def _qualified_guesses(self) -> Dict[str, int]:
        # Exclude any guess larger than the actual seconds. This may
        # be an empty set.
        actual_secs = self.stop_time - self.start_time
        qualified = {
            username: seconds
            for username, seconds in self.guesses.items()
            if seconds <= actual_secs
        }

        # Sort the remaining guesses by number of seconds. The
        # largest (last) guess is the one closest to actual_secs
        # without going over.
        return dict(
            sorted(
                qualified.items(),
                key=lambda item: item[1],
            )
        )

    #----------------------------------------------------------------------
    def send_chat(self, msg: str) -> None:
        """
        Convenience/consistency wrapper for sending messages out through
        IRC. Ensures the client is started.
        """
        global chat_client

        if chat_client is None or not chat_client.running:
            OBS.debug(f"Tried to send chat, but client is not connected. ({msg})")
            return

        chat_client.send_chat(msg)

    #----------------------------------------------------------------------
    def command_brb(self, msg: ChatMessage) -> None:
        """
        Handle a !brb command.
        """
        OBS.debug("Handling !brb.")
        # Validate command was sent by broadcaster or nod.
        if not (msg.is_mod or msg.is_broadcaster):
            OBS.debug("Only mods can start brb.")
            self.send_chat(MESSAGES.ONLY_MOD_START)
            return

        if self.running:
            OBS.debug("brb already running.")
            self.send_chat(MESSAGES.ALREADY_RUNNING)
            return

        self.guess_start()

        # Send the starting chat message.
        self.send_chat(MESSAGES.BRB_STARTED(streamer=self.user))

        OBS.debug("brb handled.")

    #----------------------------------------------------------------------
    def command_at(self, msg: ChatMessage) -> None:
        """
        Handle an !at command.
        """
        OBS.debug("Handling !at.")
        if not self.running:
            OBS.debug("No brb running.")
            self.send_chat(MESSAGES.NO_BRB)
            return

        [_cmd, time_str, *_rest] = msg.message.split()
        delta = DT.strpdelta(time_str)
        if not delta:
            OBS.debug(f"Failed to parse !at in message: {msg}")
            self.send_chat(MESSAGES.BAD_GUESS(user=msg.username))
            return

        existing_guess = self.has_guess(msg.username)
        if existing_guess:
            OBS.debug(f"User {msg.username} already has a guess registered: {existing_guess}")
            self.send_chat(MESSAGES.ALREADY_GUESSED(
                user=msg.username,
                guess=DT.strfdelta(existing_guess),
            ))
            return

        if self.add_guess(msg.username, delta):
            OBS.debug(f"Guess registered for user {msg.username}: {delta}")
            self.send_chat(MESSAGES.GUESS_ACCEPTED(
                user=msg.username,
                guess=DT.strfdelta(delta),
            ))

        OBS.debug("!at handled.")

    #----------------------------------------------------------------------
    def command_back(self, msg: ChatMessage) -> None:
        """
        Handler that's called when chat_client returns a ChatMessage with
        a !back command.
        """
        global chat_client

        OBS.debug("Handling !back.")
        # Validate command was sent by broadcaster or nod.
        if not (msg.is_mod or msg.is_broadcaster):
            OBS.debug("Only mods can run !back.")
            self.send_chat(MESSAGES.ONLY_MOD_END)
            return

        if not self.running:
            OBS.debug("No brb running.")
            self.send_chat(MESSAGES.NO_BRB)
            return

        # Disallow further guessing.
        if self.running:
            OBS.debug("Stopping guesses.")
            self.guess_end(self.auto_hide_secs)

        # Announce the winner.
        (winner, guess_secs) = self.guess_winner()
        actual_secs = self.stop_time - self.start_time
        if winner:
            OBS.debug(f"!brb winner is: {winner}")
            self.send_chat(MESSAGES.BRB_FINISHED(
                streamer=chat_client.user,
                time=DT.strfdelta(actual_secs),
                winner=winner,
                diff=DT.strfdelta(actual_secs - guess_secs),
            ))
        else:
            OBS.debug("No (valid) guesses, so no winner.")
            self.send_chat(MESSAGES.BRB_FINISHED_NO_GUESSES(
                streamer=self.user,
                time=DT.strfdelta(actual_secs),
            ))

        OBS.debug("!back handled.")


###########################################################################
# OBS Wrapper

# =========================================================================
type OBSTimer = Callable[[], None]

class OBS:
    #----------------------------------------------------------------------
    @classmethod
    def source_exists(self, source_name: str) -> bool:
        """
        Returns True if a source with source_name is already present
        in the currently active scene.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/toggle_sceneitem_vis.py#L9-L13
        """
        scene_source = S.obs_frontend_get_current_scene() # Release!
        scene = S.obs_scene_from_source(scene_source) # Don't release.
        si = S.obs_scene_find_source(scene, source_name) # Don't release unless an explicit ref was saved.
        if scene_source is not None:
            S.obs_source_release(scene_source)
        return (si is not None)

    #----------------------------------------------------------------------
    @classmethod
    def sceneitem_set_visible_by_name(self, source_name: str, visible: bool) -> bool:
        scene_source = S.obs_frontend_get_current_scene() # Release!
        scene = S.obs_scene_from_source(scene_source) # Don't release.
        si = S.obs_scene_find_source(scene, source_name) # Don't release unless an explicit ref was saved.
        if si is not None:
            if S.obs_sceneitem_visible(si) is not visible:
                self.debug(f"{'showing' if visible else 'hiding'} sceneitem.")
                S.obs_sceneitem_set_visible(si, visible)
            #S.obs_sceneitem_release(si) # DON'T release??
            S.obs_source_release(scene_source)

    #----------------------------------------------------------------------
    @classmethod
    def source_set_text_by_name(self, source_name: str, text: str) -> None:
        source = S.obs_get_source_by_name(source_name) # Release!
        if source is not None:
            debug(f"updating text contents to {text}")
            settings = S.obs_data_create() # Release!
            S.obs_data_set_string(settings, "text", text)
            S.obs_source_update(source, settings)
            S.obs_source_release(source)
            S.obs_data_release(settings)

    #----------------------------------------------------------------------
    @classmethod
    def error(self, msg: str) -> None:
        self._log(msg, S.LOG_ERROR)

    #----------------------------------------------------------------------
    @classmethod
    def warn(self, msg: str) -> None:
        self._log(msg, S.LOG_WARNING)

    #----------------------------------------------------------------------
    @classmethod
    def info(self, msg: str) -> None:
        self._log(msg, S.LOG_INFO)

    #----------------------------------------------------------------------
    @classmethod
    def debug(self, msg: str) -> None:
        self._log(msg, S.LOG_DEBUG)

    #----------------------------------------------------------------------
    @classmethod
    def _log(self, msg: str, level: int) -> None:
        # Grab our frame and all parents we don't want to exclude.
        stack = [
            f for f in inspect.stack(0)
            if f.frame.f_code.co_qualname not in [
                'dispatch.<locals>.handle',
            ]
        ]

        # The last element in the pre-filtered slice of (up to) 3 is the
        # farthest-most caller. This handles the case where there's less
        # than 2 stacks above us.
        callers_caller_frameinfo = stack[0:3][-1]

        # Dig into the frame details to get the fully qualified method name.
        calling_method = callers_caller_frameinfo.frame.f_code.co_qualname

        S.script_log(level, f"[{calling_method}] {msg}")


# TODO: Review calls to debug(), switch those appriopriate to info/warn/error().


###########################################################################
# Global State

e: Events = Events()
chat_client: TwitchEventPubClient = None


###########################################################################
# OBS Scripting API

#--------------------------------------------------------------------------
def script_description() -> str:
    """
    Describes the script in the OBS GUI.

    Uses Qt formatting with a subset of HTML.

    Ref: https://doc.qt.io/archives/qt-5.15/richtext-html-subset.html
    Ref: (for raw svg) https://dashboard.twitch.tv/ > (Nav Menu) > Moderation > Inspect icon
    Ref: (svg to data uri) https://codeshack.io/svg-to-data-uri-converter/
    Ref: (color changer) https://www.svggenie.com/tools/svg-color-changer
    """
    mod_svg = dedent("""
        <img src="data:image/svg+xml,%3Csvg width='18' height='18' viewBox='0 0 24 24'%3E%3Cpath fill='%23009900' fill-rule='evenodd' d='M15.504 2H22v6.496L10.35 17.35 12 19l-1.5 1.5-2.785-2.785L3.5 22 2 20.5l4.285-4.215L3.5 13.5 5 12l1.65 1.65L15.504 2ZM20 7.504 8.923 15.923l-.846-.846L16.496 4H20v3.504Z' clip-rule='evenodd'%3E%3C/path%3E%3C/svg%3E" alt="Broadcaster and mods only">
    """.strip())

    cell_style = 'style="background-color: #444444;"'

    return dedent(f"""
        <h3><a href="https://github.com/beporter/brb-timer">{SCRIPT_NAME}</a> v{SCRIPT_VERSION}</h3>

        <p>Let chatters guess when the streamer will return from being AFK.<br></p>

        <table cellpadding="3" width="100%">
            <tr>
                <td {cell_style} align="center">{mod_svg}</td>
                <td {cell_style}><code>{COMMANDS.BRB}</code></td>
                <td {cell_style}>Start the on-screen timer and allow guessing.</td>
            </tr>
            <tr>
                <td {cell_style} align="center">&nbsp;</td>
                <td {cell_style}><code>{COMMANDS.AT} MM:SS</code> &nbsp; </td>
                <td {cell_style}>Chatter registers their guess.</td>
            </tr>
            <tr>
                <td {cell_style} align="center">{mod_svg}</td>
                <td {cell_style}><code>{COMMANDS.BACK}</code></td>
                <td {cell_style}>Stop the timer and show the winner.</td>
            </tr>
        </table>

        <!--
        <p>The script requires Twitch API permission to look up your broadcaster (user) name, and channel name. Click the <b>Connect Twitch</b> button to start that process.</p>

        <p>The script adds a text Source named <code>{DEFAULTS.TEXT_TIMER}</code> to your active Scene. Modify as desired, but <i>leave the name untouched</i>. Connects to your Twitch chat to listen for the BRB commands listed above.</p>
        -->

        <p><i>Originally written exclusively for <a href="https://www.twitch.tv/enns">Enns</a> by <a href="https://github.com/beporter">beporter</a> in August 2026.</i></p>
    """.strip())

#--------------------------------------------------------------------------
def script_defaults(
    settings, # obs_data_t
): # -> obs_data_t
    """
    This lifecycle methods is called EARLY in the script's startup
    process. Before `script_properties()` is even called for the first
    time.
    """
    S.obs_data_set_default_int(
        settings,
        "auto_hide_secs",
        DEFAULTS.AUTO_HIDE_SECS,
    )

#--------------------------------------------------------------------------
def script_properties():
    """
    GUI controls to show in OBS. This is called pretty late in the
    startup process, so don't "modify" the controls here. (That has to
    happen in an `obs_property_set_modified_callback()` handler.)

    But the displayed controls should model the "first run" state. So
    if a button should be hidden until a dropdown entry is selected, hide
    the button in this method and let an eventual modified callback
    unhide it when conditions are met.
    """
    props = S.obs_properties_create()

    # Connect Twitch button
    b = S.obs_properties_add_button(
        props,
        'twitch_connect_button',
        'Connect Twitch',
        lambda: None,
    )
    S.obs_property_button_set_type(b, S.OBS_BUTTON_URL)
    S.obs_property_button_set_url(b, TwitchEventPubClient.KICKOFF_URL)
    S.obs_property_set_long_description(
        b,
        (
            'Open a browser window to obtain '
            'a Twitch API token for this script to use. '
            'Paste it below.'
        ),
    )

    # Twitch OAuth text input.
    t = S.obs_properties_add_text(
        props,
        'twitch_token',
        'Twitch OAuth Token',
        S.OBS_TEXT_PASSWORD,
    )
    # S.obs_property_set_modified_callback(t, dispatch(e.on_token_modified))

    OBS.debug('script_properties complete.')
    return props

#--------------------------------------------------------------------------
def script_load(settings):
    """
    Run once when OBS first initializes this script. This is basically
    the __init__ for this script as it exists in OBS. Happens before
    the GUI is ready, so you can't query scenes or sources here.
    """
    OBS.debug(f"script_load setting running = False.")
    e.running = False

    S.obs_frontend_add_event_callback(lambda ev: e.on_event(ev))

    # Start the ticker from the main python thread.
    # (It will no-op unless a !brb is running.)
    S.timer_add(e.ticker, 1 * 1000)

    OBS.debug(f"script_load complete.")

#--------------------------------------------------------------------------
def script_update(settings):
    """
    Responsible for communicating the DATA behind the GUI props from obs
    to this script. OBS will already preserve these settings, but we need
    to get them into the script's runtime state for actual usage because
    the `settings` object isn't directly available anywhere except here
    and a few other places. The thing getting updated here is this very
    script, not the settings and not OBS.
    """
    global chat_client
    OBS.debug(f"script_update starting.")

    new_token = S.obs_data_get_string(settings, 'twitch_token')
    if (
        new_token is not None
        and len(new_token) > 0
        and new_token != e.token
    ):
        e.token = new_token
        if chat_client is not None:
            OBS.debug('Closing old chat client.')
            chat_client.close()
            chat_client = None

    if len(e.token) > 0:
        OBS.debug('Starting new chat client.')
        chat_client = TwitchEventPubClient(e.token, e.on_chat)
        chat_client.start()
        if chat_client.running:
            OBS.debug(f"Setting username: {chat_client.user}")
            e.user = chat_client.user

    OBS.debug('script_update complete.')

#--------------------------------------------------------------------------
def script_unload():
    global chat_client

    S.timer_remove(e.ticker)

    if chat_client is not None:
        chat_client.close()
        chat_client = None

    OBS.debug('script_unload complete.')
