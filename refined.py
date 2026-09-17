"""
Minimal implementation of BRB Timer, starting from working timer example in simple.py.
"""
from __future__ import annotations
from dataclasses import dataclass
import datetime
import http
import inspect
import json
import select
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict

import obspython as S

SCRIPT_NAME = "BRB Timer"
SCRIPT_VERSION = 1.0

# =========================================================================
# Chat Commands and Messages

class COMMANDS:
    BRB = "!brb"
    BACK = "!back"
    AT = "!at"

"""
These next classes clean up the code that uses these format strings.
Instead of:

    str.format(MESSAGES.BRB_STARTED, streamer='some user')

You can use:

    MESSAGES.BRB_STARTED(streamer="some user")
"""
class Message(str):
    def __call__(self, **kwargs):
        return self.format(**kwargs)

class MessageMeta(type):
    def __getattribute__(cls, name):
        value = super().__getattribute__(name)
        if isinstance(value, str) and not isinstance(value, Message):
            return Message(value)
        return value

class MESSAGES(metaclass=MessageMeta):
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
        "But nobody registered any "
        f"{COMMANDS.AT} guesses so there's no winner."
    )

    # Guessing.
    BAD_GUESS = (
        "Couldn't understand {user}'s guess. "
        "Format is MINS:SECS. "
        "For example: '35:42' means you think the streamer "
        "will return in 35 mins and 42 seconds."
    )
    ALREADY_GUESSED = (
        "{user} already guessed {guess}. "
        "You can't change your guess!"
    )
    GUESS_ACCEPTED = "{user} guesses {guess}."


###########################################################################
# Twitch IRC Client

class TwitchApi:
    """
    Encapsulate http calls to Twitch's APIs.
    """

    # BRB Timer for Chat
    # by beporter@users.sourceforge.net
    # https://dev.twitch.tv/console/apps/ja5swzyzsr1euwm0e53h1sxqhk553l
    APP_CLIENT_ID = "ja5swzyzsr1euwm0e53h1sxqhk553l"
    OAUTH_SCOPES = [
        # Receive and send irc chat messages.
        # https://dev.twitch.tv/docs/api/reference/#send-chat-message
        "chat:read",
        "chat:edit",
    ]
    KICKOFF_URL = "https://beporter.github.io/brb-timer/start.html"

    #----------------------------------------------------------------------
    def __init__(self, token: str):
        self.token = token

    #----------------------------------------------------------------------
    def validate(self) -> Dict[str, any] | False:
        """
        Check the passed oauth token to determine if it's still valid,
        and who it belongs to.

        Ref: https://dev.twitch.tv/docs/authentication/validate-tokens#how-to-validate-a-token
        """
        resp = self._get('oauth2/validate')

        if not resp:
            debug("oauth2/validate failed.")
            return False

        if not resp['login']:
            debug("OAuth token is not attached to a user.")
            return False

        if not set(self.OAUTH_SCOPES).issubset(resp['scopes']):
            missing_scopes = ", ".join(set(self.OAUTH_SCOPES) - set(resp['scopes']))
            debug(
                "OAuth token is lacking necessary scopes: (%s)" % (missing_scopes),
            )
            return False

        return {
            'login': resp.get('login', ''),
            'broadcaster_id': int(resp.get('user_id', '')),
            'expires_in': resp.get('expires_in', None), # int seconds
        }

    #----------------------------------------------------------------------
    def channel(self, broadcaster_id: int) -> Dict[str, any]:
        """
        Get the provided broadcaster's channel details.

        Ref: https://dev.twitch.tv/docs/api/reference#get-channel-information
        """
        resp = self._get('helix/channels', {'broadcaster_id': broadcaster_id})

        if not resp:
            debug(
                "helix/channels failed for "
                f"broadcaster_id: {broadcaster_id}"
            )
            return False

        if not resp.get('data') or not resp.get('data', [False])[0]:
            debug(
                "No channel data returned for "
                f"broadcaster_id: {broadcaster_id}"
            )
            return False

        channel = resp.get('data')[0]

        return channel.get('broadcaster_name', '')

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def _get(
        self,
        path: str,
        params: Dict[str, str|int] = {},
        extra_headers: Dict[str, str|int] = {},
    ) -> object | False:
        server = self._server(path)
        url = f"{server}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(extra_headers))

        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
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

            debug(detail)

        return False

    #----------------------------------------------------------------------
    def _server(self, path: str) -> str:
        match path.split('/', 2)[0]:
            case 'oauth2':
                return 'https://id.twitch.tv'
            case _:
                return 'https://api.twitch.tv'

    #----------------------------------------------------------------------
    def _headers(
        self,
        extra: dict[str, str|int] = {},
    ) -> dict[str, str|int]:
        return {
            'User-Agent': f"{SCRIPT_NAME} v{SCRIPT_VERSION}",
            'Accept': 'application/json',
            'Authorization': f"Bearer {self.token}",
            'Client-ID': self.APP_CLIENT_ID,
        } | extra

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

type IrcMsgCallback = Callable[[ChatMessage], None]

class TwitchIRCClient:
    HOST = "irc.chat.twitch.tv"
    PORT = 6697
    RECONNECT_DELAY = 5

    #----------------------------------------------------------------------
    def __init__(
        self,
        channel: str,
        username: str,
        oauth: str,
        callback: IrcMsgCallback,
    ):
        self.channel = channel.lower().lstrip("#")
        self.username = username
        self.oauth = oauth.removeprefix("oauth:")
        self.callback = callback

        self.socket = None
        self.send_lock = threading.Lock()
        self.running = False
        self.thread = None

    #----------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return

        self._create_wakeup_pair()
        self._drain_wakeup()

        self.running = True
        self.thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name=f"{SCRIPT_NAME}-TwitchIRC",
        )
        self.thread.start()

    #----------------------------------------------------------------------
    def close(self) -> None:
        """
        Permanently close the client.

        The receiver thread owns the network socket and is responsible for
        closing it. The wakeup pair is only used to interrupt that thread.
        """
        self.stop()

        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=2)
            except Exception as e:
                debug(f"Failed joining Twitch IRC thread: {e!r}")

        self.thread = None
        self._destroy_wakeup_pair()
        debug("IRC client shut down.")

    #----------------------------------------------------------------------
    def stop(self) -> None:
        self.running = False
        self._wake()

    #----------------------------------------------------------------------
    def send_chat(self, message: str) -> None:
        self._send_raw(f"PRIVMSG #{self.channel} :{message}")

    #----------------------------------------------------------------------
    def _create_wakeup_pair(self) -> None:
        self.wakeup_reader, self.wakeup_writer = socket.socketpair()

    #----------------------------------------------------------------------
    def _wake(self) -> None:
        try:
            if self.wakeup_writer is not None:
                self.wakeup_writer.send(b"\x00")
        except OSError as e:
            debug(f"Failed to wake IRC thread: {e!r}")

    #----------------------------------------------------------------------
    def _drain_wakeup(self) -> None:
        if self.wakeup_reader is None:
            return

        self.wakeup_reader.setblocking(False)

        try:
            while self.wakeup_reader.recv(4096):
                pass
        except BlockingIOError:
            pass
        except OSError:
            pass
        finally:
            try:
                self.wakeup_reader.setblocking(True)
            except OSError:
                pass

    #----------------------------------------------------------------------
    def _destroy_wakeup_pair(self) -> None:
        for sock in (self.wakeup_reader, self.wakeup_writer):
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                debug(f"Failed to close IRC sockets: {e!r}")

        self.wakeup_reader = None
        self.wakeup_writer = None

    #----------------------------------------------------------------------
    def _send_raw(self, line: str) -> None:
        with self.send_lock:
            sock = self.socket

            if sock is None:
                raise ConnectionError("IRC socket is not connected")

            sock.sendall((line + "\r\n").encode("utf-8"))

    #----------------------------------------------------------------------
    def _connect(self) -> None:
        """
        Establish an SSL-wrapped socket connection.

        Ref: https://stackoverflow.com/a/23615951/70876
        """
        sock = ssl.create_default_context().wrap_socket(
            socket.create_connection(
                (self.HOST, self.PORT),
                timeout=10,
            ),
            server_hostname=self.HOST,
        )
        sock.setblocking(True)

        self.socket = sock

        # Ref: https://dev.twitch.tv/docs/chat/irc
        self._send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send_raw(f"PASS oauth:{self.oauth}")
        self._send_raw(f"NICK {self.username}")
        self._send_raw(f"JOIN #{self.channel}")

        debug("IRC client connected.")

    #----------------------------------------------------------------------
    def _receive_loop(self) -> None:
        while self.running:
            try:
                self._connect()
                self._read_loop()

            except Exception as e:
                if self.running:
                    debug(f"Twitch IRC Listener: {e!r}")

            finally:
                self._close_socket()

            if self.running:
                self._wait_for_reconnect()

    #----------------------------------------------------------------------
    def _read_loop(self) -> None:
        buffer = b""

        while self.running:
            readable, _, _ = select.select(
                [self.socket, self.wakeup_reader],
                [],
                [],
            )

            if self.wakeup_reader in readable:
                self._drain_wakeup()
                return

            if self.socket not in readable:
                continue

            data = self.socket.recv(4096)

            if not data:
                raise ConnectionError("EOF")

            buffer += data

            while b"\r\n" in buffer:
                raw_line, buffer = buffer.split(b"\r\n", 1)
                line = raw_line.decode("utf-8", errors="replace")
                self._handle_line(line)

                if not self.running:
                    return

    #----------------------------------------------------------------------
    def _wait_for_reconnect(self) -> None:
        if self.wakeup_reader is None:
            return

        try:
            readable, _, _ = select.select(
                [self.wakeup_reader],
                [],
                [],
                self.RECONNECT_DELAY,
            )

            if readable:
                self._drain_wakeup()

        except OSError as e:
            debug(f"Failed waiting for IRC reconnect: {e!r}")

    #----------------------------------------------------------------------
    def _close_socket(self) -> None:
        """
        Close the network socket.

        This is called only by the receiver thread, which owns the socket.
        """
        sock = self.socket
        self.socket = None

        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    #----------------------------------------------------------------------
    def _handle_line(self, line: str) -> None:
        if line.startswith("PING"):
            self._send_raw(line.replace("PING", "PONG", 1))
            return

        if line.startswith(":tmi.twitch.tv RECONNECT"):
            raise ConnectionError("Twitch requested reconnect")

        if (
            " NOTICE * :Login authentication failed" in line
            or " NOTICE * :Improperly formatted auth" in line
        ):
            debug("Twitch IRC authentication failed")
            self.running = False
            return

        if " PRIVMSG " not in line:
            return

        msg = self._parse_privmsg(line)
        if msg:
            self.callback(msg)

    #----------------------------------------------------------------------
    def _parse_privmsg(self, line: str) -> ChatMessage | None:
        if not line:
            return None

        try:
            tags_raw, remainder = line.split(" ", 1)
            prefix, command, channel, message = remainder.split(" ", 3)
        except ValueError:
            return None

        if not tags_raw.startswith("@"):
            return None

        if command != "PRIVMSG":
            return None

        if not prefix.startswith(":"):
            return None

        if not message.startswith(":"):
            return None

        tags = self._parse_tags(tags_raw[1:])

        username = prefix[1:].split("!", 1)[0]
        if not username:
            return None

        badges = set(tags.get("badges", "").split(","))

        return ChatMessage(
            username,
            tags.get("display-name", username),
            message[1:],
            tags.get("mod") == "1",
            "broadcaster/1" in badges,
        )

    #----------------------------------------------------------------------
    @staticmethod
    def _parse_tags(raw: str) -> Dict[str, str]:
        result = {}

        for field in raw.split(";"):
            if "=" in field:
                key, value = field.split("=", 1)
                result[key] = value

        return result


###########################################################################
# Events class

class Events:
    source_name: str = ''
    running: bool = False
    start_time: int = 0
    stop_time: int = 0
    auto_hide_secs: int = 120
    hide_time: int = 0
    guesses: dict[str, int] = {}

    token: str = ''
    channel: str = ''
    user: str = ''

    #----------------------------------------------------------------------
    def on_list_modified(self, props, prop, settings = None):
        """
        Our callback that's fired from the text source selection changing.
        Return True to tell OBS to redraw the property widgets.
        """
        # If selected `source` exists, save it to runtime settings.
        source_name = S.obs_data_get_string(settings, 'source_prop')
        timer_source = S.obs_get_source_by_name(source_name)
        if timer_source is not None:
            S.obs_source_release(timer_source)
            debug(f"timer source is valid: {source_name}")
            self.source_name = source_name # Write to runtime settings.

        debug('on_list_modified complete.')
        return True

    #----------------------------------------------------------------------
    def on_token_modified(self, props, prop, settings = None):
        debug('on_token_modified complete.')
        return True

    #----------------------------------------------------------------------
    def on_start_button(self, props, prop, settings = None):
        """
        Callback fired from GUI button click.
        """
        self.guess_start()
        self.update_buttons(props)

        debug('on_start_button complete')
        return True # Always refresh the GUI.

    #----------------------------------------------------------------------
    def on_stop_button(self, props, prop, settings = None):
        """
        Callback fired from GUI button click.

        Sets hide time for ticker to use to remove itself.
        """
        self.guess_end(self.auto_hide_secs)
        self.update_buttons(props)

        debug('on_stop_button complete')
        return True # Always refresh the GUI.

    #----------------------------------------------------------------------
    def on_chat(self, message: ChatMessage) -> None:
        """
        Invoked by the IRC client whenever a chat message is received.

        This router is only responsible for determining whether to
        respond to an event and dispatching it, or ignore it.
        """
        # TODO: Implement replies? https://dev.twitch.tv/docs/chat/irc/#replying-to-a-chat-message

        match message.message.split()[0]:
            case COMMANDS.BRB:
                self.command_brb(message)
            case COMMANDS.AT:
                self.command_at(message)
            case COMMANDS.BACK:
                self.command_back(message)
            case 'show':
                OBS2.sceneitem_set_visible_by_name(self.source_name, True)
            case 'hide':
                OBS2.sceneitem_set_visible_by_name(self.source_name, False)
            case _:
                debug('No BRB commands matched. Skipping.')

    #----------------------------------------------------------------------
    def update_buttons(self, props): # TODO: remove when start/stop buttons are removed.
        p = S.obs_properties_get(props, 'start_button')
        show_start = bool(not self.running and self.source_name)
        debug('update_buttons %s start button' % ('showing' if show_start else 'hiding'))
        S.obs_property_set_visible(p, show_start)

        p = S.obs_properties_get(props, 'stop_button')
        show_stop = bool(self.running and self.source_name)
        debug('update_buttons %s stop button' % ('showing' if show_stop else 'hiding'))
        S.obs_property_set_visible(p, show_stop)

    #----------------------------------------------------------------------
    def ticker(self):
        """
        Updates the text source contents. Scheduled as a per-second
        timer. Can only access running python state, not OBS settings or
        props for this script.
        """
        debug('ticker called.')

        if self.hide_time > 0 and int(time.time()) > self.hide_time:
            debug('hide_time reached, hiding ticker and removing self timer')
            OBS2.sceneitem_set_visible_by_name(self.source_name, False)
            OBS2.timer_remove(self.ticker)
            return

        OBS2.source_set_text_by_name(self.source_name, self.ticker_text())

        debug(f"ticker complete.")

    #----------------------------------------------------------------------
    def ticker_text(self) -> str:
        if self.running:
            diff_secs: int = int(time.time()) - self.start_time
        else:
            diff_secs: int = self.stop_time - self.start_time

        dt: datetime.datetime = datetime.datetime.fromtimestamp(diff_secs, datetime.timezone.utc)
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

            OBS2.source_set_text_by_name(self.source_name, self.ticker_text())
            # OBS2.timer_add(self.ticker, 1 * 1000)
            # OBS2.sceneitem_set_visible_by_name(self.source_name, True)

    #----------------------------------------------------------------------
    def has_guess(self, username: str) -> int | False:
        if username in self.guesses:
            return self.guesses[username]

        return False

    #----------------------------------------------------------------------
    def add_guess(self, username: str, seconds: int) -> bool:
        if not self.active:
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
        actual_secs = self.stop_time - self.start_time

        # Exclude any guess larger than the actual seconds.
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
        global irc_client

        if irc_client is None or not irc_client.running:
            debug(f"Tried to send irc chat, but client is not connected. ({msg})")
            return

        irc_client.send_chat(msg)

    #----------------------------------------------------------------------
    def command_brb(self, msg: ChatMessage) -> None:
        """
        Handle a !brb command.
        """
        debug("Handling !brb.")
        # Validate command was sent by broadcaster or nod.
        if not (msg.is_mod or msg.is_broadcaster):
            debug("Only mods can start brb.")
            self.send_chat(MESSAGES.ONLY_MOD_START)
            return

        if self.running:
            debug("brb already running.")
            self.send_chat(MESSAGES.ALREADY_RUNNING)
            return

        self.guess_start() # TODO: This is causing a crash. But only when called from irc, not from on_start_button

        # Send the starting chat message.
        self.send_chat(MESSAGES.BRB_STARTED(streamer=self.user))

        debug("brb handled.")

    #----------------------------------------------------------------------
    def command_at(self, msg: ChatMessage) -> None:
        """
        Handle an !at command.
        """
        debug("Handling !at.")
    #     if not self.is_guessing_running():
    #         OBS.debug("No brb running.")
    #         self.send_chat(MESSAGES.NO_BRB)
    #         return

    #     parsed_delta = self._parse_at(msg.message)
    #     if not parsed_delta:
    #         OBS.error(f"Failed to parse !at in message: {msg}")
    #         self.send_chat(MESSAGES.BAD_GUESS(user=msg.username))
    #         return

    #     existing_guess = self.guesses.has_guess(msg.username)
    #     if existing_guess:
    #         OBS.warn(f"User {msg.username} already has a guess registered: {existing_guess}")
    #         self.send_chat(MESSAGES.ALREADY_GUESSED(
    #             user=msg.username,
    #             guess=DT.strfdelta(existing_guess),
    #         ))
    #         return

    #     if self.guesses.add_guess(msg.username, parsed_delta.total_seconds()):
    #         OBS.debug(f"Guess registered for user {msg.username}: {parsed_delta}")
    #         self.send_chat(MESSAGES.GUESS_ACCEPTED(
    #             user=msg.username,
    #             guess=DT.strfdelta(parsed_delta),
    #         ))

    #     OBS.debug("!at handled.")

    #----------------------------------------------------------------------
    def command_back(self, msg: ChatMessage) -> None:
        """
        Handler that's called when self.irc returns a ChatMessage with
        a !back command.
        """
        debug("Handling !back.")
    #     # Validate command was sent by broadcaster or nod.
    #     if not (msg.is_mod or msg.is_broadcaster):
    #         OBS.debug("Only mods can run !back.")
    #         self.send_chat(MESSAGES.ONLY_MOD_END)
    #         return

    #     if not self.is_guessing_running():
    #         OBS.debug("No brb running.")
    #         self.send_chat(MESSAGES.NO_BRB)
    #         return

    #     # Disallow further guessing.
    #     if self.is_guessing_running():
    #         OBS.debug("Stopping guesses.")
    #         self.guesses.end(self.settings.auto_hide_secs)

    #     # Announce the winner.
    #     (winner, guess_secs) = self.guesses.winner()
    #     if winner:
    #         OBS.debug(f"!brb winner is: {winner}")
    #         self.send_chat(MESSAGES.BRB_FINISHED(
    #             streamer=self.settings.twitch_username,
    #             time=DT.duration_str(DT.duration_secs_to_dt(self.guesses.elapsed_time().total_seconds())),
    #             winner=winner,
    #             diff=DT.duration_str(self.guesses.actual_secs - guess_secs),
    #         ))
    #     else:
    #         OBS.debug("No guesses, so no winner.")
    #         self.send_chat(MESSAGES.BRB_FINISHED_NO_GUESSES(
    #             streamer=self.settings.twitch_username,
    #             time=DT.duration_str(self.guesses.actual_secs),
    #         ))
    #     OBS.debug("!back handled.")

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    # def _parse_at(self, msg: str) -> datetime.timedelta | False:
    #     """
    #     Ref: timedelta https://stackoverflow.com/a/51916936/70876
    #     Ref: regex https://stackoverflow.com/a/8318367/70876
    #     """
    #     [_cmd, time_str, *_rest] = msg.split()
    #     delta = DT.strpdelta(time_str)
    #     if not delta:
    #         OBS.warn('Could not parse !at command argument: %s' % (msg))
    #         return False

    #     return delta


###########################################################################
# Timers

type OBSTimer = Callable[[], None]

class OBS2:
    #----------------------------------------------------------------------
    @classmethod
    def timer_add(self, timer: OBSTimer, millisecs: int) -> OBSTimer:
        """
        Creates a new global function named like the provided timer
        function and adds that global function to OBS as a timer.

        Usage:
            OBS.timer_add(instance.my_func, millisecs)
        """
        func_name = timer.__name__
        if func_name in globals().keys():
            raise ValueError(
                "Can't shadow the provided timer. Global function "
                f"already exists: {func_name}"
            )

        main = sys.modules[__name__]
        setattr(main, func_name, dispatch(timer))
        shadow = getattr(main, func_name)
        S.timer_add(shadow, millisecs)
        return shadow

    #----------------------------------------------------------------------
    @classmethod
    def timer_remove(self, timer: OBSTimer, throw: bool = False) -> bool:
        func_name = timer.__name__
        if func_name not in globals().keys():
            msg = str(
                f"Can't remove the shadow for the provided timer: {timer} "
                f"Global function doesn't exist: {func_name}"
            )
            if throw:
                raise ValueError(msg)
            else:
                debug(msg)
                return False

        main = sys.modules[__name__]
        shadow = getattr(main, func_name)
        S.timer_remove(shadow)
        delattr(main, func_name)
        return True

    #----------------------------------------------------------------------
    @classmethod
    def sceneitem_set_visible_by_name(self, source_name: str, visible: bool) -> bool:
        scene_source = S.obs_frontend_get_current_scene() # Release!
        scene = S.obs_scene_from_source(scene_source) # Don't release.
        si = S.obs_scene_find_source(scene, source_name) # Don't release unless an explicit ref was saved.
        if si is not None:
            if S.obs_sceneitem_visible(si) is not visible:
                debug(f"{'showing' if visible else 'hiding'} sceneitem.")
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


###########################################################################
# Global Methods

#--------------------------------------------------------------------------
def debug(msg: str):
    # Grab our frame and at most three parent frames.
    stack = [f for f in inspect.stack(0) if f.frame.f_code.co_qualname not in ['dispatch.<locals>.handle']]
    callers_caller_frameinfo = stack[0:3][-1]
    calling_method = callers_caller_frameinfo.frame.f_code.co_qualname
    S.script_log(S.LOG_DEBUG, f"[{calling_method}] {msg}")

#--------------------------------------------------------------------------
def dispatch(callback: callable) -> callable:
    S.script_log(S.LOG_DEBUG, f"Creating closure for {callback.__qualname__}")
    def handle(*args, **kwargs):
        S.script_log(S.LOG_DEBUG, f"Triggering {callback.__qualname__}")
        return callback(*args, **kwargs)
    return handle


###########################################################################
# Global State

e: Events = Events()
irc_client: TwitchIRCClient = None


###########################################################################
# OBS Scripting API

#--------------------------------------------------------------------------
def script_description():
    debug('script_description complete.')
    return 'Testing modifying properties in realtime.'

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

    # Select text source list.
    p = S.obs_properties_add_list( # TODO: replace with auto-create-source
        props,
        'source_prop',
        "Text Source",
        S.OBS_COMBO_TYPE_LIST,
        S.OBS_COMBO_FORMAT_STRING,
    )
    S.obs_property_set_modified_callback(p, dispatch(e.on_list_modified))
    sources = S.obs_enum_sources()
    if sources is not None:
        for source in sources:
            source_id = S.obs_source_get_unversioned_id(source)
            if source_id == "text_gdiplus" or source_id == "text_ft2_source":
                name = S.obs_source_get_name(source)
                S.obs_property_list_add_string(p, name, name)

        S.source_list_release(sources)

    b = S.obs_properties_add_button(
        props,
        'twitch_connect_button',
        'Connect Twitch',
        lambda: None,
    )
    S.obs_property_button_set_type(b, S.OBS_BUTTON_URL)
    S.obs_property_button_set_url(b, TwitchApi.KICKOFF_URL)
    S.obs_property_set_long_description(
        b,
        (
            'Open a browser window to obtain '
            'a Twitch API token for this script to use. '
            'Paste it below.'
        ),
    )

    t = S.obs_properties_add_text(
        props,
        'twitch_token',
        'Twitch OAuth Token',
        S.OBS_TEXT_PASSWORD,
    )
    S.obs_property_set_modified_callback(t, dispatch(e.on_token_modified))

    # Button to start the timer. (Use the built-in callback, not set_modified_callback)
    start_button = S.obs_properties_add_button( # TODO: remove
        props,
        'start_button',
        'Start timer',
        dispatch(e.on_start_button),
    )

    # Button to stop the timer.
    stop_button = S.obs_properties_add_button( # TODO: remove
        props,
        'stop_button',
        'Stop timer',
        dispatch(e.on_stop_button),
    )
    S.obs_property_set_visible(stop_button, False) # Hide until a valid text source is selected.

    debug('script_properties complete.')
    return props

#--------------------------------------------------------------------------
def script_load(settings):
    """
    Run once when OBS first initializes this script. This is basically
    the __init__ for this script as it exists in OBS. Happens before
    the GUI is ready, so you can't query scenes or sources here.
    """
    debug(f"script_load setting running = False.")
    e.running = False
    debug(f"script_load importing source_name from settings.")
    e.source_name = S.obs_data_get_string(settings, 'source_prop')

    # from inspect import getmembers, isfunction
    # obs_funcs = [x[0] for x in getmembers(S, isfunction)]
    # for i in range(0, len(obs_funcs), 20):
    #     print(obs_funcs[i:i+20])
    # print(dict(getmembers(sys.modules[__name__], isfunction)).keys())

    debug(f"script_load complete.")

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
    global irc_client
    debug(f"script_update starting.")
    e.source_name = S.obs_data_get_string(settings, 'source_prop')

    new_token = S.obs_data_get_string(settings, 'twitch_token')
    if (
        new_token is not None
        and len(new_token) > 0
        and new_token != e.token
    ):
        e.token = new_token
        api = TwitchApi(e.token)
        valid = api.validate()
        if valid:
            e.user = valid['login']
            e.channel = api.channel(valid['broadcaster_id'])

            S.obs_data_set_string(settings, 'twitch_user', e.user)
            S.obs_data_set_string(settings, 'twitch_channel', e.channel)

        if irc_client is not None:
            irc_client.close()
            irc_client = None

    if (
        len(e.token) > 0
        and len(e.user) > 0
        and len(e.channel) > 0
        and irc_client is None
    ):
        irc_client = TwitchIRCClient(
            e.channel,
            e.user,
            e.token,
            e.on_chat,
        )
        irc_client.start()

    debug('script_update complete.')

#--------------------------------------------------------------------------
def script_save(settings):
    debug('script_save called.')

#--------------------------------------------------------------------------
def script_unload():
    global irc_client

    if irc_client is not None:
        irc_client.close()
        irc_client = None

    debug('script_unload complete.')
