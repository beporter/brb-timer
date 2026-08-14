"""
BRB Timer for OBS Studio
https://github.com/beporter/brb-timer

"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from textwrap import dedent
import http.client as http_client
import http.server
import inspect
import json
import logging
import os.path
from pprint import pprint
import queue
import secrets
import socket
import ssl
import sys
import threading
import time
from typing import Callable, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

logging.basicConfig(level=logging.DEBUG)
http_client.HTTPConnection.debuglevel = 1
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True

try:
    import obspython as obs # type: ignore
except ImportError:
    # This bit of code replaces the real obspython module with a
    # testing mock. The script won't "work" without obs, but it'll
    # let you run `python -i brb_timer.py` to access an interactive
    # terminal without failing on
    # `NameError: name 'obs' is not defined.`
    from unittest.mock import Mock
    obs = Mock()

    logging.debug('obs module unavailble. Replaced with a mock.')


###########################################################################
# Constants
###########################################################################

SCRIPT_NAME = "BRB Timer"

# =========================================================================
# OBS Settings Defaults

class DEFAULTS:
    BOT_USERNAME = "BRBot"
    AUTO_HIDE_SECS = 120
    TIMER_TEXT = f"{SCRIPT_NAME} init! 00:00"

class SOURCES:
    TEXT_TIMER = f"{SCRIPT_NAME} - Timer"
    TRANSITION_SHOW = f"{SCRIPT_NAME} - Show Transition"
    TRANSITION_HIDE = f"{SCRIPT_NAME} - Hide Transition"

# =========================================================================
# Chat Commands and Messages

class COMMANDS:
    BRB = "!brb"
    BACK = "!back"
    AT = "!at"

class MESSAGES:
    ONLY_MOD_START = f"Only mods can start a {COMMANDS.BRB}."
    ONLY_MOD_END = f"Only mods can end a {COMMANDS.BRB} with {COMMANDS.BACK}."
    BRB_STARTED = (
        "{streamer} is taking a break! "
        "Guess how long till they're back (without going over!) "
        "Type `{COMMANDS.AT} MM:SS` in chat!"
    )
    ALREADY_RUNNING = f"{COMMANDS.BRB} is already running! Must use {COMMANDS.BACK} first."
    GUESS_ACCEPTED = "{user} guesses {guess}."
    ALREADY_GUESSED = "{user} already guessed {guess}. You can't change your guess!"
    NO_BRB = (f"There's no {COMMANDS.BRB} running. A mod must use {COMMANDS.BRB} first.")
    BRB_FINISHED = ("{streamer} is back! Winner is: {winner}")

# =========================================================================
# Twitch OAuth

class TWITCH:
    # BRB Timer for Chat
    # by beporter@users.sourceforge.net
    # https://dev.twitch.tv/console/apps/ja5swzyzsr1euwm0e53h1sxqhk553l
    APP_CLIENT_ID = "ja5swzyzsr1euwm0e53h1sxqhk553l"

# This EXACT URI needs to be registered as the OAuth redirect URI
# for your Twitch application.
    REDIRECT_HOST = "127.0.0.1"
    REDIRECT_PATH = "/oauth/callback"
    @staticmethod
    def redirect_uri(self, port: int) -> str:
        return f"http://{self.TWITCH_REDIRECT_HOST}:{port}{self.TWITCH_REDIRECT_PATH}"

    AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
    TOKEN_URL = "https://id.twitch.tv/oauth2/token";

    OAUTH_SCOPES = [
        # Join chat as "you" but appear as a bot.
        # https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelchatmessage
        # https://dev.twitch.tv/docs/api/reference/#send-chat-message
        "user:bot",

        # Read the list of channel followers.
        # https://dev.twitch.tv/docs/api/reference#get-channel-followers
        "moderator:read:followers",

        # Read channel moderators (requires mod access to channel).
        # https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelmoderate-v2
        "moderator:read:moderators",

        # Receive and send irc chat messages.
        # https://dev.twitch.tv/docs/api/reference/#send-chat-message
        "chat:edit",

        # Other scopes we might end up needing:

        # Join channel as a bot.
        # https://dev.twitch.tv/docs/api/reference/#send-chat-message
        # "channel:bot,"

        # Read list of moderators.
        # https://dev.twitch.tv/docs/api/reference#get-moderators
        # "moderation:read",

        # Read list of chat participants.
        # https://dev.twitch.tv/docs/api/reference#get-chatters
        # "moderator:read:chatters",

        # Read chat.
        # https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/#channelchatmessage
        # "user:read:chat",

        # Write chat messages.
        # https://dev.twitch.tv/docs/api/reference/#send-chat-message
        # "user:write:chat",
    ]

    CALLBACK_HTML = r"""
<!DOCTYPE html>
<html lang="en-US">
<head>
    <meta charset="utf-8">
    <title>Twitch Authorization</title>
</head>
<body>
    <h2>Connecting to Twitch...</h2>
    <p id="status">Please wait.</p>

    <script>
    (async function() {
        const status = document.getElementById("status");

        try {
            // Twitch implicit grant puts the OAuth response in the URL fragment, not the query string.
            const fragment = window.location.hash.substring(1);

            if (!fragment) {
                throw new Error("No OAuth response was received.");
            }

            const params = new URLSearchParams(fragment);
            const data = {};
            for (const [key, value] of params.entries()) {
                data[key] = value;
            }

            // Send the fragment contents to our localhost Python server.
            const response = await fetch("/oauth/complete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                throw new Error(
                    "Local OAuth server returned HTTP " + response.status
                );
            }

            status.textContent = "Twitch authorization complete. You can close this window.";

            // Remove the token from the browser's visible URL.
            window.history.replaceState(
                {},
                document.title,
                window.location.pathname
            );

        } catch (error) {
            console.error(error);
            status.textContent = "Authorization failed: " + error.message;
        }
    })();
    </script>
</body>
</html>
"""


###########################################################################
# Script State Storage
###########################################################################

class BRBState:
    """
    Holds the current BRB session state.

    That includes whether a !brb is running, what chatters have guessed, and who the most recent winner is.

    State machine:

    IDLE
        |
        | !brb
        V
    RUNNING
        |
        | !back
        V
    FINISHED
        |
        | auto-hide timer expires
        V
    IDLE

    """

    #######################################################################
    def __init__(self) -> None:
        self.reset()

    #######################################################################
    def reset(self) -> None:
        self.active: bool = False

        self.start_time = None
        self.stop_time = None

        self.guesses: Dict[str, int] = {}

        self.winner: Optional[str] = None
        self.last_result: Optional[str] = None


###########################################################################
# Script Settings Storage
###########################################################################

@dataclass
class BRBSettings(object):
    """
    Holds the script's internal settings/config data.

    Acts as a wrapper for the data that we need to have persisted by
    OBS between launches.

    This class does NOT need to store anything from `script_properties()`
    since OBS already handles that. This class is for values not intended
    to be exposed to the user.

    Typing is critical here to ensure our to_data() method calls the
    right obs module method.
    """
    twitch_oauth_auth_token: str = None
    twitch_oauth_access_token: str = None
    twitch_oauth_expiry_at: int = 0

    twitch_broadcaster_id: int = None
    twitch_username: str = None
    twitch_channel: str = None

    obs_timer_source_uuid: str = None
    obs_transition_show_uuid: str = None
    obs_transition_hide_uuid: str = None

    auto_hide_secs: int = DEFAULTS.AUTO_HIDE_SECS

    # guesses: Dict[str, int] = {}
    # winner: Optional[str] = None
    # last_result: Optional[str] = None

    # TODO: No longer needed?
    # def __post_init__(self, **args):
    #     super().__init__(**args)
    #     self.reset()

    def __setattr__(self, name, value):
        """
        Prevent the addition of arbitrary new attributes.

        This means trying to set `settings.new_attr = 'value'` will
        raise an AttributeError.
        """
        if not hasattr(self, name):
            raise AttributeError(f"Invalid setting '{name}'.")

        object.__setattr__(self, name, value)

        return value

    #######################################################################
    # def reset(self) -> None:
    #     pass # TODO: No longer needed?

    #######################################################################
    def from_data(self, data):
        # Ref: https://docs.obsproject.com/reference-settings#get-functions
        # This is dumb, but it's easier for python to parse json than OBS's `obs_data_t` objects.
        json_str = obs.obs_data_get_json(data)
        parsed = json.load(json_str)
        attrs = [attr for attr in vars(self) if not name.startswith("__")]
        for name, value in parsed.items():
            if name in attrs: # TODO: This might not work for merging. Could overwrite a valid value with None.
                setattr(self, name, value)

    #######################################################################
    def to_data(self):
        data = obs.obs_create_data()
        for name in dir(self):
            val = getattr(self, name)
            match type(val).__name__:
                case 'int':
                    obs.obs_data_set_int(data, name, val)
                case 'str':
                    obs.obs_data_set_string(data, name, val)
                case _:
                    obs.obs_data_set_string(data, name, val)

        obs.obs_release_data(data)

        return data

    #######################################################################
    def to_json(self):
        out = {}
        for name in vars(self):
            if not name.startswith("__"):
                value = getattr(self, name)
                out[name] = value

        return json.dumps(out)


###########################################################################
# Chat Message Data Container
###########################################################################

@dataclass
class ChatMessage:
    username: str
    display_name: str
    message: str
    is_mod: bool
    is_broadcaster: bool


###########################################################################
# Twitch IRC Client
###########################################################################

class TwitchIRCClient:
    """
    Although Twitch now recommends EventSub for many integrations, chat over IRC is still supported and is perfectly adequate for this project.

    The IRC client should have one responsibility:

    socket
        ↓
    IRC parsing
        ↓
    ChatMessage objects
        ↓
    CommandParser

    It knows nothing about `!brb`, `!at`, timers, guesses, etc. and encodes its message parsing into ChatMessage payloads to keep raw IRC lines out of the rest of the app.
    """

    HOST = "irc.chat.twitch.tv"
    PORT = 6697
    RECONNECT_DELAY = 5

    def __init__(
        self,
        channel: str,
        username: str,
        oauth: str,
        callback: Callable[[ChatMessage], None],
    ):
        self.channel = channel.lower().lstrip("#")
        self.username = username
        self.oauth = oauth.removeprefix("oauth:")
        self.callback = callback

        self.socket = None
        self.reader = None
        self.send_lock = threading.Lock()
        self.running = False
        self.thread = None

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    #######################################################################
    def start(self):
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(
            target=self.receive_loop,
            daemon=True,
            name=f"{SCRIPT_NAME}-TwitchIRC",
        )

        self.thread.start()

    #######################################################################
    def stop(self):
        self.running = False

        try:
            if self.socket:
                self.socket.close()
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #######################################################################
    def receive_loop(self):
        while self.running:
            try:
                self.connect()
                while self.running:
                    line = self.reader.readline()
                    if not line:
                        raise ConnectionError("EOF")

                    self.handle_line(line.rstrip())

            except Exception as ex:
                self._log(f"TwitchIRC: {ex}", obs.WARNING)
                try:
                    if self.socket:
                        self.socket.close()
                except Exception:
                    pass

                time.sleep(self.RECONNECT_DELAY)

    #######################################################################
    def connect(self):
        raw = socket.create_connection((self.HOST, self.PORT), timeout=15)
        ctx = ssl.create_default_context()
        self.socket = ctx.wrap_socket(raw, server_hostname=self.HOST)
        self.reader = self.socket.makefile(
            "r",
            encoding="utf-8",
            newline="\r\n",
        )

        self.send_raw(f"PASS oauth:{self.oauth}")
        self.send_raw(f"NICK {self.username}")

        # Ref: https://dev.twitch.tv/docs/chat/irc
        self.send_raw("CAP REQ :twitch.tv/tags")
        self.send_raw("CAP REQ :twitch.tv/commands")

        self.send_raw(f"JOIN #{self.channel}")

    #######################################################################
    def send_chat(self, message: str):
        self.send_raw(f"PRIVMSG #{self.channel} :{message}")

    #######################################################################
    def handle_line(self, line: str):
        if line.startswith("PING"):
            self.send_raw(line.replace("PING", "PONG", 1))

            return

        if " PRIVMSG " not in line:
            return

        msg = self.parse_privmsg(line)
        if msg:
            self.callback(ChatMessage(msg))

    #######################################################################
    def parse_privmsg(self, line: str) -> Optional[ChatMessage]:
        try:
            tags_raw, remainder = line.split(" ", 1)
            tags = self.parse_tags(tags_raw[1:])
            prefix, _command, _channel, message = (
                remainder.split(" ", 3)
            )
            username = prefix[1:].split("!")[0]
            message = message[1:]

            return ChatMessage(
                username=username,
                display_name=tags.get("display-name", username),
                message=message,
                is_mod=(tags.get("mod") == "1"),
                is_broadcaster=("broadcaster/1" in tags.get("badges", "")),
            )

        except Exception:
            return None

    #######################################################################
    def send_raw(self, line: str):
        with self.send_lock:
            self.socket.sendall((line + "\r\n").encode("utf-8"))

    #######################################################################
    @staticmethod
    def parse_tags(raw: str) -> Dict[str, str]:
        result = {}

        for field in raw.split(";"):
            if "=" in field:
                k, v = field.split("=", 1)
                result[k] = v

        return result

    #######################################################################
    @staticmethod
    def _log(self, message, level = obs.LOG_ERROR):
        obs.script_log(level, f"[{self.__class__.__name__}] {message}")


###########################################################################
# Source Generator (Text Source with show/hide scene_item transitions)
###########################################################################

class SourceGenerator:
    """
    Creates an OBS text source in the currently active OBS scene if it does not
    already exist.

    The generated source:
      - Has the configured name
      - Displays the configured text
      - Gets a show transition
      - Gets a hide transition
      - Is added to the current scene
      - Is saved through OBS's normal source-saving mechanism
    """

    SHOW_TRANSITION_TYPE = "slide_transition"
    HIDE_TRANSITION_TYPE = "slide_transition"
    SHOW_TRANSITION_DIR = "left"
    HIDE_TRANSITION_DIR = "right"
    TRANSITION_DURATION_MS = 300

    def __init__(
        self,
        source_name,
        text="hello world",
        text_source_id=None,
    ):
        self.text_source_name = source_name
        self.text = text
        self.transition_duration = 300 # TODO: Change this to BRBScript.settings.auto_hide_delay

        # OBS uses different built-in text source IDs on different platforms.
        if text_source_id is None:
            if sys.platform.startswith("win"):
                self.text_source_ids = [
                    "text_gdiplus_v2",
                    "text_gdiplus",
                ]
            else:
                self.text_source_ids = [
                    "text_ft2_source_v2",
                    "text_ft2_source",
                ]
        else:
            self.text_source_ids = [text_source_id]

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    #######################################################################
    @staticmethod
    def source_exists(self, source_name):
        """
        Returns True if a source with source_name is already present
        in the currently active scene.
        """
        scene_source = obs.obs_frontend_get_current_scene()
        if scene_source is None:
            self._log("No active scene.", obs.LOG_WARNING)

            return False

        try:
            scene = obs.obs_scene_from_source(scene_source)
            if scene is None:
                return False

            # Might need to switch to the `_recursive` variant.
            item = obs.obs_scene_find_source(scene, source_name)

            return item is not None

        finally:
            obs.obs_source_release(scene_source)

    #######################################################################
    @contextmanager
    def create_source(self):
        """
        If we were provided a text_source_name that doesn't exist in the
        active scene, create one and add it in using some defaults for
        the OBS user to subsequently tweak.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5a57/src/add_nested.py#L16
        Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/plugins/text-freetype2/text-freetype2.c#L170
        """
        if self.source_exists(self.text_source_name):
            self._log(
                "Source '%s' already exists in scene '%s'; "
                "Nothing created."
                % (self.source_name, scene_name),
                obs.LOG_INFO,
            )
            return False

        # Create the text source for the timer display
        with self._create_text_source() as source:
            obs.obs_save_source(source)
            text_source_uuid = obs.obs_get_source_uuid(source)

            # Add source to scene.
            # Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/libobs/obs.h#L153
            with self._current_scene() as scene:
                if scene is None:
                    self._log("Current frontend source is not an OBS scene.")
                    return False

                try:
                    scene_name = obs.obs_get_scene_name(scene)

                    # Add transitions to scene item.
                    sceneitem = obs.obs_scene_add(scene, source)
                    if sceneitem is None:
                        self._log(
                            f"Could not add source '{self.text_source_name}' to scene '{scene_name}'.",
                        )
                        return False

                    show_uuid = self.create_transition_source(sceneitem, "show", "left", text_source_uuid)
                    hide_uuid = self.create_transition_source(sceneitem, "hide", "right", text_source_uuid)

                except Exception as exc:
                    self._log(
                        "Exception while creating source '%s': %s"
                        % (self.text_source_name, exc),
                    )

                    return False

                finally:
                    # Clean up.
                    if sceneitem is not None:
                        obs.obs_sceneitem_release(sceneitem)

        self._log(
            "Created OBS source:\n"
            "<Timer Text Source name='%s', uuid='%s', type='%s' contents='%s'>\n"
            "<Active Scene name='%s', sceneitem_id='%s'>\n"
            "<Show Transition Source name='%s' uuid='%s'>\n"
            "<Hide Transition Source name='%s' uuid='%s' duration=%dms>\n"
            % (
                obs.obs_source_get_name(source),
                obs.obs_source_get_uuid(source),
                obs.obs_source_get_id(source),
                self.text,

                scene_name,
                obs.obs_sceneitem_get_id(sceneitem),

                obs.obs_source_get_name(obs.obs_get_source_by_uuid(show_uuid)),
                show_uuid,
                self.self.transition_duration,

                obs.obs_source_get_name(obs.obs_get_source_by_uuid(hide_uuid)),
                hide_uuid,
                self.transition_duration,
            ),
            obs.LOG_INFO,
        )

        return True

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    #######################################################################
    def create_transition_source(
        self,
        sceneitem,
        visibility: str,
        direction: str,
        target_uuid: str,
    ) -> str|False:
        # Input validation.
        if visibility not in ("show", "hide"):
            raise ValueError("visibility must be 'show' or 'hide'")

        if direction not in ("left", "right"):
            raise ValueError("direction must be 'left' or 'right'")

        # Create the source containing the transition.
        with self._data_ar() as settings:
            obs.obs_data_set_string(settings, "direction", direction)
            transition = obs.obs_source_create(
                "slide_transition",
                getattr(SOURCES, "TRANSITION_%s" % visibility.upper()),
                settings,
                None,
            )

        if transition is None:
            self._log(f"Failed to create {visibility} slide_transition source.")
            return False

        # Find the scene_item for the timer text source.
        with self._current_scene() as scene:
            target_item = None
            items = obs.obs_scene_enum_items(scene)
            try:
                for item in items:
                    source = obs.obs_sceneitem_get_source(item)
                    if source is not None:
                        if obs.obs_source_get_uuid(source) == target_uuid:
                            target_item = item
                            break
            finally:
                obs.sceneitem_list_release(items)

        if target_item is None:
            self._log(f"Source UUID {target_uuid!r} is not present in the current scene.")
            return False

        try:
            # Attach the transition to the scene item and set duration.
            obs.obs_sceneitem_set_transition(target_item, visibility == "show", transition)
            obs.obs_sceneitem_set_transition_duration(
                target_item,
                visibility == "show",
                self.transition_duration,
            )

            # Set on-screen position to top-right.
            vi = obs.obs_video_info()
            obs.obs_get_video_info(vi)
            obs.obs_sceneitem_set_alignment(sceneitem, obs.OBS_ALIGN_RIGHT | obs.OBS_ALIGN_TOP)
            obs.obs_sceneitem_set_pos(sceneitem, obs.vec2(vi.base_width, 0))

            # Set z-index to top.
            obs.obs_sceneitem_set_order(sceneitem, obs.OBS_ORDER_MOVE_TOP) # layer on top

            # Set item bounds.
            # obs.obs_sceneitem_set_bounds_type(sceneitem, obs.OBS_BOUNDS_SCALE_TO_WIDTH)
            # obs.obs_sceneitem_set_bounds_alignment(sceneitem, obs.OBS_ALIGN_CENTER)
            # obs.obs_sceneitem_set_bounds(sceneitem, obs.vec2(400, 100))

            uuid = obs.obs_source_get_uuid(transition)

        finally:
            obs.obs_source_release(transition)

        return uuid

    #######################################################################
    def _create_text_source(self):
        """
        Create the first text source type available in this OBS build.
        """
        settings = obs.obs_data_create()
        try:
            obs.obs_data_set_string(settings, "text", self.text)

            for source_id in self.text_source_ids:
                # Check whether this source type exists before trying to
                # create it.
                display_name = obs.obs_source_get_display_name(source_id)

                if display_name is None:
                    continue

                source = obs.obs_source_create(
                    source_id,
                    self.text_source_name,
                    settings,
                    None,
                )

                if source is not None:
                    return source

            return None

        finally:
            obs.obs_data_release(settings)

    ###########################################################################
    @contextmanager
    def _get_source(self):
        """
        Internal helper that yields the OBS text source containing our timer.
        Returns false when the source name or the source itself isn't present.
        Auto-releases the source after use.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5a57/src/add_nested.py#L16

        Usage example:

        with self._get_source() as s:
            # do something with `s`.
        """
        if not self.text_source_name:
            return False

        source = obs.obs_get_source_by_name(self.text_source_name)

        if source is None:
            return False

        try:
            yield source
        finally:
            obs.obs_source_release(source)

    ###########################################################################
    @contextmanager
    def _text_settings(self):
        """
        Internal helper that yields an obs_data object pre-configured with
        our stock text Source settings.

        Usage:

            with self._text_settings() as settings:
                obs.obs_source_create(id, name, settings, None)
        """
        settings = obs.obs_data_create()

        try:
            obs.obs_data_set_string(settings, "text", self.text)
            #obs.obs_data_set_int(settings, "custom_width", 500)
            #obs.obs_data_set_bool(settings, "word_wrap", True)

            obs.obs_data_set_bool(settings, "outline", True)
            obs.obs_data_set_bool(settings, "drop_shadow", False)
            # Ref: https://docs.obsproject.com/reference-properties#c.obs_properties_add_color_alpha
            obs.obs_data_set_int(settings, "color1", 0xffffff00) # Format: 0xrrggbbaa
            obs.obs_data_set_int(settings, "color2", 0xffffff00)

            with self._data_ar() as font:
                obs.obs_data_set_string(font, "face", "Monaco")
                obs.obs_data_set_string(font, "style", "Regular")
                obs.obs_data_set_int(font, "size", 256)
                obs.obs_data_set_int(font, "flags", 0)

                obs.obs_data_set_obj(settings, "font", font)

            yield settings

        finally:
            obs.obs_data_release(settings)

    ###########################################################################
    @contextmanager
    def _data_ar(self, source_settings=None):
        """
        Internal helper that yields an obs_data object either from the
        passed settings, or from scratch. Auto-releases after use.

        Usage example:

        with self._data_ar() as d:
            # do something with `d`.
        """
        if source_settings:
            settings = obs.obs_source_get_settings(source_settings)
        else:
            settings = obs.obs_data_create()

        try:
            yield settings

        finally:
            obs.obs_data_release(settings)

    ###########################################################################
    @contextmanager
    def _current_scene(self):
        """
        Internal helper that yields an obs_scene object and auto-releases
        afterward.

        Usage example:

        with self._current_scene() as scene:
            # do something with `scene`.
        """
        try:
            scene_source = obs.obs_frontend_get_current_scene()
            scene = obs.obs_scene_from_source(scene_source)

            yield scene

        finally:
            obs.obs_source_release(scene_source)
            obs.obs_scene_release(scene)

    #######################################################################
    @staticmethod
    def _log(self, message, level = obs.LOG_ERROR):
        obs.script_log(level, f"[{self.__class__.__name__}] {message}")



###########################################################################
# Timer Renderer
###########################################################################

class TimerRenderer:
    """
    This class handles the OBS display of the on-screen timer.

    It requires the obspython module loaded as `obs`.
    """

    def __init__(self):
        self.text_source_name: str = SOURCES.TEXT_TIMER
        self.visible: bool = False
        self.cached_text: str = ""
        self.source_uuid: str = None

    #######################################################################
    def configure(self, source_name: str):
        """
        Called from script_update() -> BRBScript -> configure() whenever the user changes settings.
        """
        self.text_source_name = source_name

        if not SourceGenerator.source_exists(source_name):
            generator = SourceGenerator(source_name, self.cached_text)
            self.source_uuid = generator.create_source()

    #######################################################################
    def toggle(self):
        """
        Toggle a source's visibility.

        Ref: https://github.com/BraatheSaaS/OBS-Studio-Python-Scripting-Cheatsheet-obspython-Examples-of-API#toggle-sceneitem-visibility

        Returns the newly toggled state. True = visible, False = hidden.
        """
        current_scene = obs.obs_scene_from_source(obs.obs_frontend_get_current_scene())
        scene_item = obs.obs_scene_find_source(current_scene, self.text_source_name)
        toggled = not obs.obs_sceneitem_visible(scene_item)
        self.visible = toggled
        obs.obs_sceneitem_set_visible(scene_item, toggled)

        return toggled

    #######################################################################
    def show(self):
        self.visible = True
        self._apply_text(self.cached_text)

    #######################################################################
    def hide(self):
        self.visible = False
        self._apply_text("")

    #######################################################################
    def set_text(self, text: str):
        self.cached_text = text

        if self.visible:
            self._apply_text(text)

    #######################################################################
    def clear(self):
        self.cached_text = ""
        self.hide()

    #######################################################################
    def _apply_text(self, text: str):
        if not self.text_source_name:
            return

        source = obs.obs_get_source_by_name(
            self.text_source_name
        )
        if source is None:
            return

        settings = obs.obs_data_create()
        try:
            obs.obs_data_set_string(
                settings,
                "text",
                text,
            )
            obs.obs_source_update(
                source,
                settings,
            )

        finally:
            obs.obs_data_release(settings)
            obs.obs_source_release(source)


###########################################################################
# Guess Manager
###########################################################################

class GuessManager:
    def __init__(self, state: BRBState):
        self.state = state

    #######################################################################
    def clear(self) -> None:
        self.state = None

    #######################################################################
    def add_guess(self, username: str, seconds: int) -> bool:
        if username not in self.state.guesses.keys:
            self.state.guesses[username] = seconds

            return True # Guess added.

        return False # User already has a guess registered.

    #######################################################################
    def find_winner(self, actual_seconds: int):
        qualified = dict(filter(lambda secs: secs <= actual_seconds, self.state.guesses.items()))
        dict(sorted(qualified.items(), key=lambda secs: secs))
        winner = qualified.keys()[-1]

        return winner


###########################################################################
# Twitch API Wrapper
###########################################################################

class TwitchApi:

    def __init__(self, client_id, token):
        self.client_id = client_id
        self.token = token

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    #######################################################################
    def user(self, username):
        resp = self._get('helix/users', {'login': username})

        return resp # TODO: Refine this.

    #######################################################################
    def validate(self) -> Dict[str, any] | False:
        resp = self._get('oauth2/validate')

        if not resp:
            self._log("oauth2/validate failed.")
            return False

        if not resp['login']:
            self._log("OAuth token is not attached to a user.")
            return False

        if TWITCH.OAUTH_SCOPES not in resp['scopes']:
            self._log(
                "OAuth token is lacking necessary scopes: (%s)" %
                (set(TWITCH.OAUTH_SCOPES) - set(resp['scopes'])).join(", ")
            )
            return False

        # TODO: if response['scopes'] not include [...]

        return resp # TODO: Refine this.

        # username = response['login']  # (username)
        # user_id = response['user_id']  # (user_id)

    #######################################################################
    def channel(self, broadcaster_id):
        # Ref: https://api.twitch.tv/helix/streams?user_login=$user&type=live&first=1

        resp = self._get('helix/channels', {'broadcaster_id': broadcaster_id})

        return resp # TODO: Refine this.

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #######################################################################
    def _get(self, path, params = {}, extra_headers = {}):
        server = self._server(path)
        url = f"{server}/{path}?" + urllib.parse.urlencode(params)
        logging.debug(url) # TODO: remove
        logging.debug(extra_headers)# TODO: remove
        req = urllib.request.Request(url, headers=self._headers(extra_headers))

        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                try:
                    detail = json.load(e["message"])
                except Exception:
                    detail = "unauthorized"

#                 raise RuntimeError(f"Twitch: {detail}") from e
#
#             raise
            logging.debug(e) # TODO: Refine

        return False

    #######################################################################
    def _server(self, path):
        match path.split('/', 2)[0]:
            case 'oauth2':
                return 'https://id.twitch.tv'
            case _:
                return 'https://api.twitch.tv'

    #######################################################################
    def _headers(self, extra = {}):
        return {
            'User-Agent': os.path.basename(inspect.stack()[0][1]),
            'Accept': 'application/json',
            'Authorization': f"Bearer {self.token}",
            'Client-ID': self.client_id,
        } | extra



###########################################################################
# HTTP server
###########################################################################

class TwitchOAuthHandler(http.server.BaseHTTPRequestHandler):

    #######################################################################
    def setCallback(self, callback: Callable) -> None:
        """
        Give ourselves a way of sending data back to BRBScript when we successfully process a POST submission containing our OAuth details.
        """
        # TODO: There's no opportunity for us to call this before the server starts in a different thread.
        self.callback = callback

    #######################################################################
    def setServer(self, server) -> None:
        """
        This non-standard hook lets us shut our own parent http server down after successful POST processing.
        """
        # TODO: There's no opportunity for us to call this before the server starts in a different thread.
        self.server = server

    #######################################################################
    def do_GET(self):
        """
        Handles the successful Twitch OAuth redirect payload.

        The Twitch auth token is passed in the URL _fragment_, so it is NOT
        available here. We return an HTML payload containing JavaScript
        that reads window.location.hash and POSTs it back to us.
        """

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != TWITCH.REDIRECT_PATH:
            self.respond(404, "text/html; charset=utf-8", "<h1>Not found.</h1>")

            return

        self.respond(200, "text/html; charset=utf-8", TWITCH.CALLBACK_HTML.encode("utf-8"))

    #######################################################################
    def do_POST(self):
        """
        Receives the OAuth information extracted from the URL fragment
        by callback.html.
        """

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth/complete":
            self.respond(404, "application/json", b'{"error":true,"status":404,"message":"Not found."}')

            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))

        except Exception as e:
            self._log("Invalid POST:", e)
            self.send_response(400)
            self.end_headers()

            return

        # TODO: Refactor. Probably signal/message BRBScript instance to save oauth details to obs settings.
        # handle_oauth_result(data)
        if not self.callback(data):
            self.respond(500, "application/json", b'{"error":true,"status":500,"message":"Server error."}')

        self.respond(200, "application/json", b'{"ok":true}')

        # server.shutdown() is called from another thread so we don't
        # deadlock the HTTP request thread processing this call to
        # do_POST().
        if self.server is not None:
            threading.Thread(
                target=self.server.shutdown,
                daemon=True
            ).start()

    #######################################################################
    def respond(self, code: int, content_type: str, body: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    #######################################################################
    def log_message(self, format, *args):
        level = obs.DEBUG
        message = format % args
        obs.script_log(level, f"[{self.__class__.__name__}] {message}")


###########################################################################
# Twitch OAuth wrapper
###########################################################################

class TwitchOAuth:

    def __init__(self):
        self.server = None
        self.server_thread = None

        self.server_ready = threading.Event()

        self.oauth_state = None

        # The most recently obtained Twitch token information.
        self.oauth_result = {}

        # Lock protecting runtime state.
        self.state_lock = threading.Lock()


    #######################################################################
    # OAuth flow
    #######################################################################

    #######################################################################
    def starter(self, pressed):
        if not pressed:
            return

        # Prevent multiple simultaneous OAuth flows.
        with self.state_lock:

            if (
                self.server_thread is not None
                and self.server_thread.is_alive()
            ):
                print(
                    "[Twitch OAuth] OAuth flow already running."
                )
                return

            self.oauth_result = {}

            # Cryptographically random state value.
            self.oauth_state = secrets.token_urlsafe(32)

        # Start HTTP server.
        self.server_ready.clear()

        self.server_thread = threading.Thread(
            target=self.run_oauth_server,
            daemon=True,
            name=f"{SCRIPT_NAME} Twitch OAuth Receiver"
        )

        self.server_thread.start()

        # Wait until the server has successfully bound its port.
        if not self.server_ready.wait(timeout=2.0):
            print(
                "[Twitch OAuth] ERROR: "
                "Timed out starting callback server."
            )

            return

        if self.server is None:
            print(
                "[Twitch OAuth] ERROR: "
                "Callback server failed to start."
            )

            return

        # Build Twitch authorization URL.
        params = {
            "response_type": "token",
            "client_id": TWITCH.APP_CLIENT_ID,
            "redirect_uri": TWITCH.redirect_uri(),
            "scope": " ".join(TWITCH.OAUTH_SCOPES),
            "state": self.oauth_state,
        }

        authorization_url = (
            TWITCH.AUTHORIZE_URL
            + "?"
            + urllib.parse.urlencode(params)
        )

        print(
            "[Twitch OAuth] Opening Twitch authorization..."
        )

        webbrowser.open(authorization_url)


    #######################################################################
    def run_server(self):
        try:
            self.server = TwitchOAuthServer()
            self.server_ready.set()

            print(
                "[Twitch OAuth] Listening on:",
                TWITCH.redirect_uri()
            )

            self.server.serve_forever()

        except OSError as e:
            print(
                "[Twitch OAuth] Could not start server:",
                e
            )

            self.server_ready.set()

        finally:
            if self.server is not None:
                try:
                    self.server.server_close()
                except Exception:
                    pass

            self.server = None

            print("[Twitch OAuth] Callback server stopped.")


    #######################################################################
    def handle_oauth_result(self, data):
        """
        Called when the browser sends the Twitch OAuth fragment back to us.
        """

        state = data.get("state")

        with self.state_lock:

            # Verify the state parameter.
            if not self.oauth_state or state != self.oauth_state:
                print(
                    "[Twitch OAuth] ERROR: Invalid OAuth state."
                )

                self.oauth_result = {
                    "error": "invalid_state"
                }

                return

            # Twitch reports authorization failure like:
            #
            # error=access_denied
            # error_description=...
            #
            if "error" in data:
                print(
                    "[Twitch OAuth] Authorization failed:",
                    data.get("error"),
                    data.get("error_description", "")
                )

                self.oauth_result = {
                    "error": data.get("error", ""),
                    "error_description": data.get(
                        "error_description",
                        ""
                    ),
                }

                return

            access_token = data.get("access_token")

            if not access_token:
                print(
                    "[Twitch OAuth] ERROR: No access token received."
                )

                self.oauth_result = {
                    "error": "missing_access_token"
                }

                return

            self.oauth_result = {
                "access_token": access_token,
                "token_type": data.get(
                    "token_type",
                    "bearer"
                ),
                "scope": data.get(
                    "scope",
                    ""
                ),
                "state": state,
            }

            print(
                "[Twitch OAuth] Successfully received access token."
            )

            print(
                "[Twitch OAuth] Scopes:",
                data.get("scope", "")
            )

            # Don't print the actual access token to the OBS log.


###########################################################################
# OAuth processing
###########################################################################

class TwitchOAuthServer(http.server.ThreadingHTTPServer):

    allow_reuse_address = True

    def __init__(self):
        # TODO: Make the port dynamic based on availability
        super().__init__(
            (TWITCH.REDIRECT_HOST, TWITCH.REDIRECT_PORT),
            TwitchOAuthHandler
        )

    #######################################################################
    @staticmethod
    def _log(self, message, level = obs.LOG_ERROR):
        obs.script_log(level, f"[{self.__class__.__name__}] {message}")


###########################################################################
# OBS Property Factory
###########################################################################

class OBSPropsFactory:
    """
    This class serves a few important purposes:

    1. It encapsulates the variety, order and release of the raw
       `obs.property_*()` functions to ensure a consistent API for this
       script to use.
    2. Because the obspython bridges into C++ code, it's
       unpythonic/unintuitve and hard to find what you need in the docs.
       This class acts as a documentation bridge by demonstrating proper
       usage of the raw `obs` module functions.
    3. It (theoretically) consolidates calls to the `obs` module, making
       the rest of the script easier to test in isolation.

    This class is "sparse". It only defines methods for the types of
    properties this script needs to function.
    """
    def __init__(self, props_container):
        self.props = props_container

    #######################################################################
    def text(self, type: int, name: str, desc: str = "") -> None:
        obs.obs_properties_add_text(
            self.props,
            name,
            desc if desc is not None else desc.replace("_", " ").capitalize(),
            type,
        )

    #######################################################################
    def twitch_button(
        self,
        keyword: str,
        url: str = None,
        text_type: str = obs.OBS_TEXT_INFO_NORMAL,
    ) -> None:
        if url is not None:
            return self.url_button(
                f"{keyword}_twitch",
                f"{keyword.capitalize()} Twitch",
                getattr(script, f"twitch_creds_{keyword}"),
                script.twitch_auth_url(),
                text_type,
            )
        else:
            return self.button(
                f"{keyword}_twitch",
                f"{keyword.capitalize()} Twitch",
                getattr(script, f"twitch_creds_{keyword}"),
                text_type,
            )

    #######################################################################
    def url_button(
        self,
        base_name: str,
        desc: str,
        callback: Callable,
        url: str,
        text_type: int,
        wrap: bool = True,
    ):
        b = self.button(base_name, desc, callback, text_type, wrap)
        obs.obs_property_button_set_type(b, obs.OBS_BUTTON_URL)
        obs.obs_property_button_set_url(b, url)

        return b # In case any further modification is desired.

    #######################################################################
    def button(
        self,
        base_name: str,
        desc: str,
        callback: Callable,
        text_type: int,
        wrap: bool = True,
    ):
        b = obs.obs_properties_add_button(
            self.props,
            f"{base_name}_button",
            desc,
            callback,
        )
        # Ref: https://docs.obsproject.com/reference-properties#c.obs_property_set_long_description
        i = obs.obs_properties_add_text(self.props, f"{base_name}_info", obs.OBS_TEXT_INFO)
        obs.obs_property_text_set_info_type(i, text_type)
        obs.obs_property_text_set_info_word_wrap(i, wrap)

        return b # In case any further modification is desired.

    #######################################################################
    def text_source_list(self, name: str, desc: str) -> None:
        # Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/duplicate_source.py#L51-L63
        sources = obs.obs_enum_sources()
        if sources is not None:
            p = obs.obs_properties_add_list(
                self.props,
                name,
                desc,
                obs.OBS_COMBO_TYPE_EDITABLE,
                obs.OBS_COMBO_FORMAT_STRING,
            )
            obs.obs_property_list_add_string(p, "(Add New)", "__add_new__") # TODO: Register a callback for this property list? Something needs to trigger the creation of the new source when a user selects (and saves?) this option in the GUI.
            for source in sources:
                # There doesn't seem to be another way to identify these.
                type = obs.obs_source_get_icon_type(source)
                if type != obs.OBS_ICON_TYPE_TEXT:
                    continue

                source_id = obs.obs_source_get_unversioned_id(source)
                name = obs.obs_source_get_name(source)
                obs.obs_property_list_add_string(p, name, source_id)

            i = obs.obs_properties_add_text(self.props, f"{name}_info", obs.OBS_TEXT_INFO)
            obs.obs_property_text_set_info_type(i, obs.OBS_TEXT_INFO_NORMAL)
            obs.obs_property_text_set_info_word_wrap(i, True)

            obs.source_list_release(sources)

    #######################################################################
    def int_with_unit(
        self,
        name: str,
        desc: str,
        min: int,
        max: int,
        step: int,
        unit: str,
    ) -> None:
        a = obs.obs_properties_add_int(self.props, name, desc, min, max, step)
        obs.obs_property_int_set_suffix(a, unit)




###########################################################################
# Main Controller
###########################################################################

class BRBScript:
    """
    This class serves as the scripts main controller.

    It provides methods for the OBS hook functions to call, in order to keep all business logic consolidated here.

    Classes used by BRBScript are expected not to use any method from the `obs` module unless that's their sole purview. This is mostly accomplished with dependency injection-- passing in handlers and callbacks to the classes.

    Architecture:

        BRBScript -----------------------------------------------|
            |          v        |        v       |               |
            |      TwitchApi    |   BRBSettings  |               |
            v                   v                v               v
        TwitchOauth       TwitchIRCClient   GuessManager    TimerRenderer
            |                   |                |               |
            v                   v                v               v
        TwitchOAuthServer  ChatMessage        BRBState       SourceGenerator
            |
            v
        TwitchOAuthHandler

    Example lifecycle:

    * OBS Launches ->
        * script_defaults() called
            * ✅ provide default property values.
        * script_description() called
            * ✅ provide static string.
        * script_load() called (obs frontend isn't active yet! Can't enumerate sources, for example.)
            * initialize BRBScript state & BRBSettings
                * check for and load existing twitch oauth creds
                * ✅ Register event handlers. `obs.signal_handler_connect()`?
        * script_update() called once
            * saves any new settings.

    * User opens the script's Properties pane in the GUI ->
        * script_properties() called
            * construct script properties for display in GUI.
        * whenever a property value is changed ->
            * script_update() called.

    * OBS_FRONTEND_EVENT_FINISHED_LOADING event fires ->
        * handler:
            * ✅ creates text source if it doesn't exist.
            * registers event handlers to:
                * create IRC background thread when streamer goes live
                * kill irc when streaming stops

    * "Connect Twitch" button clicked ->
        * BRBScript:
            * launches a local webserver thread
            * opens browser window.
    * Web server thread handles POST successfully ->
        * calls self.callback(data) -> writes oauth creds to script's OBS internal settings
        * web server thread shuts down.

    * Streamer starts broadcasting ->
        * handler starts irc thread
        * resets BRBState
    * script_tick() called for every rendered frame ->
        * (We do nothing.)
    * Mod enters !brb in chat -> irc client notifies BRBScript -> BRBScript:
        * updates GuessManager state
        * tells IRC to print chat message.
    * Chatters enter !at commands -> irc client notifies BRBScript -> BRBScript:
        * updates GuessManager state
        * prints chat message.
    * Mod enters !back command -> irc client notifies BRBScript -> BRBScript:
        * updates GuessManager state
        * prints chat message.
    * Streamer stops broadcasting ->
        * handler stops irc thread
        * resets BRBState

    * OBS shuts down ->
        * script_save() -> save any settings
        * script_unload() -> clean up any remaining threads.

    Ref: https://obsproject.com/kb/scripting-guide#script-lifecycle
    """

    def __init__(self):
        self.reset()

    #######################################################################
    def reset(self):
        self.settings = BRBSettings()
        self.state = BRBState()
        self.guesses = GuessManager(self.state)
        self.renderer = TimerRenderer()
        self.irc: Optional[TwitchIRCClient] = None
        self.oauth_server: Optional[TwitchOAuthServer] = None

    #######################################################################
    def settings_merge(self, new_settings):
        self.settings.from_data(new_settings)

    #######################################################################
    # Lifecycle Event Handlers
    #######################################################################

    #######################################################################
    def on_load(self, settings):
        """
        Called from script_load() during first OBS initialization.

        The OBS frontend won't be loaded yet, so all we need to do is
        register our various event handlers.
        """

        self.reset()
        obs.obs_frontend_add_event_callback(self.event_router)

        # Register additional signal handlers for specific objects+events
        # Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/scene_sig_con.py#L23
        # sh = obs.obs_source_get_signal_handler(source)
        # obs.signal_handler_connect(sh, "item_add", callback)
        # obs.obs_source_release(source)

    #######################################################################
    def event_router(self, event):
        """
        Invoked by OBS whenever an event is fired.

        This router is only responsible for determining whether to
        respond to an event, or ignore it.
        """
        match event:
            case obs.OBS_FRONTEND_EVENT_FINISHED_LOADING:
                self.on_obs_ready()
            # TODO: Streaming starts. `obs_frontend_streaming_start()?`
            # TODO: Streaming ends `obs_frontend_streaming_stop()`?
            case _:
                pass

    #######################################################################
    def on_obs_ready(self):
        """
        Handles the  OBS event.

        Once the OBS frontend is ready, we can check for the timer text
        source, verify oauth creds, and any other startup tasks.
        """

        # TODO: Implement

        # global _oauth_result

        # access_token = obs.obs_data_get_string(
        #     settings,
        #     "twitch_access_token"
        # )

        # token_type = obs.obs_data_get_string(
        #     settings,
        #     "twitch_token_type"
        # )

        # scopes = obs.obs_data_get_string(
        #     settings,
        #     "twitch_scopes"
        # )

        # if access_token:
        #     _oauth_result = {
        #         "access_token": access_token,
        #         "token_type": token_type,
        #         "scope": scopes,
        #     }

        #     print(
        #         "[Twitch OAuth] Existing token loaded."
        #     )


        # Auto-create the text source in the current scene when not already present.
        generator = SourceGenerator(
            source_name = SOURCES.TEXT_TIMER,
            text = f"{SCRIPT_NAME} Init: 00:00",
        )

        if generator.source_exists():
            self._log(
                f"Timer Text Source '{SOURCES.TEXT_TIMER}' is already present in the current scene.",
                obs.LOG_INFO,
            )
        elif generator.create():
            self._log(
                f"Created new Timer Text Source '{SOURCES.TEXT_TIMER}' successfully.",
                obs.LOG_INFO,
            )
        else:
            self._log(
                f"Failed to create new Timer Text Source '{SOURCES.TEXT_TIMER}'.",
            )

    #######################################################################
    def start(self):
        config = obs.obs_frontend_get_profile_config()
        token = obs.config_get_string(config, "Twitch", "Token")

        if token is None:
            self._log('OBS not logged into Twitch.', obs.INFO)
            return

        api = TwitchApi(TWITCH.APP_CLIENT_ID, token)
        channel = api.channel()
        username = api.username()
        callback = callable[ChatMessage] # TODO hook this up

        # TODO: Don't do this till streaming starts.
        self.irc = TwitchIRCClient(channel, username, token, callback)

    #######################################################################
    def shutdown(self):
        raise NotImplementedError
        # TODO: Stop all threads.
        # Save settings.

    #######################################################################
    # Chat Command Callbacks
    #######################################################################

    #######################################################################
    def command_router(self, message: ChatMessage) -> None:
        """
        Invoked by the IRC client whenever a chat message is received.

        This router is only responsible for determining whether to
        respond to an event, or ignore it.
        """
        match message.message.split()[0]:
            case COMMANDS.BRB:
                self.command_brb(message)
            case COMMANDS.AT:
                self.command_at(message)
            case COMMANDS.BACK:
                self.command_back(message)
            case _:
                pass

    #######################################################################
    def command_brb(self, msg: ChatMessage):
        """
        Handler that's called when self.irc returns a ChatMessage with a !brb commands.
        """
        raise NotImplementedError
        # TODO: Move this where it needs to go.
        # Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/README.md?plain=1#L472
        # eg.source_name = obs.obs_data_get_string(settings, "source")
        # obs.timer_remove(eg.update_text)
        # if eg.source_name != "":
        #     S.timer_add(eg.update_text, 1 * 1000)


    #######################################################################
    # TODO: Remove?
    def __stubbed_usage_examples() -> None:
        renderer.set_text("00:00")
        renderer.show()

        renderer.set_text("04:31")

        renderer.set_text(
            "04:31\nWinner: @Alice -0:15"
        )

        renderer.hide()

    #######################################################################
    def command_back(self, msg: ChatMessage):
        """
        Handler that's called when self.irc returns a ChatMessage with a !back commands.
        """
        raise NotImplementedError

    #######################################################################
    def command_at(self, msg: ChatMessage):
        """
        Handler that's called when self.irc returns a ChatMessage with an !at commands.
        """
        raise NotImplementedError

    #######################################################################
    # OBS
    #######################################################################

    #######################################################################
    def tick(self):
        # We don't need to do anything on every rendered frame.
        pass

    #######################################################################
    def twitch_channel(self) -> str:
        raise NotImplementedError
        # TODO: Call TwitchApi.channel() (when available), otherwise return empty string.

    #######################################################################
    def twitch_creds_present(self) -> bool:
        raise NotImplementedError
        # TODO: Return True when script has a non-expired access token in OBS local storage. False when no creds present in storage.

    #######################################################################
    def twitch_creds_expired(self) -> bool:
        raise NotImplementedError
        # TODO: Return True when script has an expired access token in OBS local storage.

    #######################################################################
    def twitch_creds_connect(self):
        raise NotImplementedError
        # TODO: Start local https server thread, open browser window.
        # self.oauth_flow_start()

    #######################################################################
    def twitch_creds_disconnect(self):
        raise NotImplementedError
        # TODO: Remove script stored twitch settings from OBS.

    #######################################################################
    def twitch_creds_reconnect(self):
        self.twitch_creds_disconnect()
        self.twitch_creds_connect()

    #######################################################################
    def twitch_auth_url(self):
        # TODO: Construct the full authorization request URL to be opened in the user's default browser window, to request the OAuth scopes this script needs.
        pass

    #######################################################################
    # Internal Helpers
    #######################################################################

    #######################################################################
    def oauth_flow_start(self):
        raise NotImplementedError
        # TODO: start background http server thread, open browser URL.
        # TODO: Implement opening an in-OBS browser with for Twitch OAuth flow with proper scopes and capturing the resulting token and refresh token in script's obs storage. How OBS does it in C++:
        # Ref: https://github.com/obsproject/obs-studio/blob/14e3dae77f9893a15d69c8b7bae57ac8ab961f59/frontend/oauth/TwitchAuth.cpp#L210

    #######################################################################
    def oauth_flow_end(self):
        raise NotImplementedError
        # TODO: stop the background http server thread

    #######################################################################
    @staticmethod
    def _log(self, message, level = obs.LOG_ERROR):
        obs.script_log(level, f"[{self.__class__.__name__}] {message}")



###########################################################################
# Global Script Instance
###########################################################################

script = BRBScript()


###########################################################################
# OBS Script API
###########################################################################

###########################################################################
def script_description():
    """
    Uses some kind of Qt formatting.

    Ref: TODO: Find the ref.
    """
    return dedent(f"""
        BRB Timer (<a href=\"https://github.com/beporter/brb-timer\">github.com/beporter/brb-timer</a>_

        Let chatters guess when the streamer will return from being AFK.

        * Mods can use {COMMANDS.BRB} to start the on-screen timer and allow guessing.
        * Chatters can use <code>{COMMANDS.AT} MM:SS</code> to enter a single gueess.
            * <i>Chatters can't guess multiple times because they could just keep updating their guess as the timer gets higher and higher.</i>"
        * Mods can end the guessing when the streamer returns with {COMMANDS.BACK}.
            * The closest without going over will be displayed as winner in the chat.
    """.strip())

###########################################################################
def script_properties():
    props = obs.obs_properties_create()
    factory = OBSPropsFactory(props)

    if script.twitch_creds_expired():
        # "reonnect_twitch_button" calls `script.twitch_creds_reconnect()`
        factory.twitch_button("reconnect", None, obs.OBS_TEXT_INFO_WARNING)
    elif script.twitch_creds_present():
        # "disconnect_twitch_button" calls `script.twitch_creds_disconnect()`
        factory.twitch_button("disconnect", None, obs.OBS_TEXT_INFO_DANGER)
    else:
        # "connect_twitch_button" calls `script.twitch_creds_connect()` and opens a browser window
        factory.twitch_button("connect", script.twitch_auth_url(), obs.OBS_TEXT_INFO_NORMAL)

    factory.text(obs.OBS_TEXT_DEFAULT, "channel_name")

    factory.text(obs.OBS_TEXT_DEFAULT, "bot_username")

    factory.text_source_list(
        "text_source",
        "Timer Text Source",
        (
            f"Select the OBS Text Source from the active scene to use "
            "to display the count-up timer when {COMMANDS.BRB} is active. "
            "To have the script create one for you, choose 'Add New'."
        ),
    )

    factory.int_with_unit(
        "auto_hide_delay",
        "Auto-hide Delay (seconds)",
        0,       # min
        30 * 60, # max (30 mins)
        1,       # step
        "secs",  # unit
    )

    return props

###########################################################################
def script_defaults(settings):
    """
    This lifecycle methods is called EARLY in the script's startup process. Before `script_properties()` is even called for the first time.

    The defaults defined here must track with the properties defined above in script_properties().

    If a given property is conditional or doesn't have a default, it still gets a comment here in the proper order to keep the two functions in lock step and so project-wide search turns up both places consistently.
    """

    # "connect_twitch_button": no default

    # "disconnect_twitch_button": no default

    # "reconnect_twitch_button": no default

    # TODO: Default to TwitchApi.channel() value, when available.
    obs.obs_data_set_default_string(
        settings,
        "channel_name",
        script.twitch_channel(),
    )

    obs.obs_data_set_default_string(
        settings,
        "bot_username",
        DEFAULTS.BOT_USERNAME,
    )

    obs.obs_data_set_default_string(
        settings,
        "text_source",
        "__add_new__"
    )

    obs.obs_data_set_default_int(
        settings,
        "auto_hide_delay",
        DEFAULTS.AUTO_HIDE_SECS,
    )

###########################################################################
def script_load(settings):
    """
    Called once when the script is loaded.
    """

    script.on_load(settings)

    # TODO: Refactor all logging across all OBS hook functions and all classes to use a global static class, so `Log::debug()`, and that calls `obs.script_log(obs.DEBUG, ...)` internally.
    obs.script_log(obs.LOG_INFO, f"{SCRIPT_NAME} loaded.")

###########################################################################
def script_update(settings):
    """
    Called by OBS when the user has made modifications to the scripts configuration in the GUI.

    Most relevant to us is if a "Connect Twitch" button was clicked and we have new oauth creds incoming soon.

    Ref: https://docs.obsproject.com/scripting#script_update
    """

    script.settings_merge(settings)


    # script.renderer.configure(
    #     obs.obs_data_get_string(
    #         settings,
    #         "text_source",
    #     )
    # )

###########################################################################
def script_save(settings):
    """
    Called before OBS saves the script settings to the OBS user's local storage.
    """

    with _state_lock:

        access_token = _oauth_result.get(
            "access_token",
            ""
        )

        token_type = _oauth_result.get(
            "token_type",
            ""
        )

        scopes = _oauth_result.get(
            "scope",
            ""
        )

    obs.obs_data_set_string(
        settings,
        "twitch_access_token",
        access_token
    )

    obs.obs_data_set_string(
        settings,
        "twitch_token_type",
        token_type
    )

    obs.obs_data_set_string(
        settings,
        "twitch_scopes",
        scopes
    )

###########################################################################
def script_unload():
    """
    Called when OBS unloads the script.
    """

    # TODO: Make sure any known text_source is disabled and any running timer is stopped/ended cleanly.

    global _server

    server = _server

    if server is not None:

        try:
            server.shutdown()
        except Exception:
            pass

        try:
            server.server_close()
        except Exception:
            pass

    print(
        "[Twitch OAuth] Script unloaded."
    )

    obs.script_log(obs.LOG_INFO, "BRB Timer unloaded.")




###########################################################################
# TODO: Remove the below once it has been integrated.
############################################################
### skeleton obs script with http server

## Unused?

# class TwitchOauth:

# from http.server import BaseHTTPRequestHandler, HTTPServer
# from urllib.parse import urlparse, parse_qs
# import threading


# class CallbackHandler(BaseHTTPRequestHandler):

#     def do_GET(self):
#         parsed = urlparse(self.path)

#         print("REDIRECT URL:", self.path)

#         if parsed.path == "/callback":
#             params = parse_qs(parsed.query)

#             code = params.get("code", [None])[0]

#             print("CODE:", code)

#             self.send_response(200)
#             self.send_header("Content-Type", "text/html")
#             self.end_headers()

#             self.wfile.write(
#                 b"<h1>Login complete. You can close this window.</h1>"
#             )

#     def log_message(self, format, *args):
#         pass


# def start_server():
#     server = HTTPServer(
#         ("127.0.0.1", 8765),
#         CallbackHandler
#     )

#     server.serve_forever()


# threading.Thread(
#     target=start_server,
#     daemon=True
# ).start()


# TODO: Remove the below once it has been integrated.
"""
From `/Users/beporter/Library/Application Support/obs-studio/basic/scenes/Untitled.json`
In OBS Menus: File > Show Settings Folder
Navigate through: basic > scenes

    "sources": [
        {
            "prev_ver": 536870916,
            "name": "Scene",
            "uuid": "db8355a7-f1c1-44ef-8149-ffc9f6a3bec5",
            "id": "scene",
            "versioned_id": "scene",
            "settings": {
                "id_counter": 1,
                "custom_size": false,
                "items": [
                    {
                        "name": "example_text_source",
                        "source_uuid": "91595f92-fffe-4d92-8f31-ccf64f503526",
                        "visible": false,
                        "locked": false,
                        "rot": 0.0,
                        "scale_ref": {
                            "x": 1920.0,
                            "y": 1080.0
                        },
                        "align": 5,
                        "bounds_type": 0,
                        "bounds_align": 0,
                        "bounds_crop": false,
                        "crop_left": 0,
                        "crop_top": 0,
                        "crop_right": 0,
                        "crop_bottom": 0,
                        "id": 1,
                        "group_item_backup": false,
                        "pos": {
                            "x": 0.0,
                            "y": 0.0
                        },
                        "pos_rel": {
                            "x": -1.7777777910232544,
                            "y": -1.0
                        },
                        "scale": {
                            "x": 1.0,
                            "y": 1.0
                        },
                        "scale_rel": {
                            "x": 1.0,
                            "y": 1.0
                        },
                        "bounds": {
                            "x": 0.0,
                            "y": 0.0
                        },
                        "bounds_rel": {
                            "x": 0.0,
                            "y": 0.0
                        },
                        "scale_filter": "disable",
                        "blend_method": "default",
                        "blend_type": "normal",
                        "show_transition": {
                            "id": "slide_transition",
                            "versioned_id": "slide_transition",
                            "name": "example_text_source Show Transition",
                            "transition": {
                                "direction": "left"
                            },
                            "duration": 300
                        },
                        "hide_transition": {
                            "id": "slide_transition",
                            "versioned_id": "slide_transition",
                            "name": "example_text_source Hide Transition",
                            "transition": {
                                "direction": "right"
                            },
                            "duration": 300
                        },
                        "private_settings": {}
                    }
                ]
            },
            "mixers": 0,
            "sync": 0,
            "flags": 0,
            "volume": 1.0,
            "balance": 0.5,
            "enabled": true,
            "muted": false,
            "push-to-mute": false,
            "push-to-mute-delay": 0,
            "push-to-talk": false,
            "push-to-talk-delay": 0,
            "hotkeys": {
                "OBSBasic.SelectScene": [],
                "libobs.show_scene_item.1": [],
                "libobs.hide_scene_item.1": []
            },
            "deinterlace_mode": 0,
            "deinterlace_field_order": 0,
            "monitoring_type": 0,
            "canvas_uuid": "6c69626f-6273-4c00-9d88-c5136d61696e",
            "private_settings": {}
        },
        {
            "prev_ver": 536870916,
            "name": "example_text_source",
            "uuid": "91595f92-fffe-4d92-8f31-ccf64f503526",
            "id": "text_ft2_source",
            "versioned_id": "text_ft2_source_v2",
            "settings": {
                "text": "hello world",
                "font": {
                    "face": "Monaco",
                    "style": "Regular",
                    "size": 256,
                    "flags": 0
                },
                "outline": true,
                "color2": 4294967295,
                "drop_shadow": false
            },
            "mixers": 0,
            "sync": 0,
            "flags": 0,
            "volume": 1.0,
            "balance": 0.5,
            "enabled": true,
            "muted": false,
            "push-to-mute": false,
            "push-to-mute-delay": 0,
            "push-to-talk": false,
            "push-to-talk-delay": 0,
            "hotkeys": {},
            "deinterlace_mode": 0,
            "deinterlace_field_order": 0,
            "monitoring_type": 0,
            "private_settings": {}
        }
    ],
"""


# Twitch API Notes

# https://dev.twitch.tv/console/apps/ja5swzyzsr1euwm0e53h1sxqhk553l
# Personal BRBTimer OAuth Client ID: ja5swzyzsr1euwm0e53h1sxqhk553l
# Personal OAuth token from active WaterFox session cookie: 3d1d84bcdb97f386f7c96b7fbcca7c7e
#
#
# Manual authorize url:
# https://id.twitch.tv/oauth2/authorize?client_id=ja5swzyzsr1euwm0e53h1sxqhk553l&redirect_uri=http://localhost:3000&response_type=token&scope=user:bot%20moderator:read:followers%20moderator:read:moderators%20chat:edit&state=manual123

# Redirected URL:
# http://localhost:3000/#
# access_token=z0i4z35akghba2z9caiq84oys3acyv
# &scope=user%3Abot+moderator%3Aread%3Afollowers+moderator%3Aread%3Amoderators+chat%3Aedit
# &state=manual123
# &token_type=bearer
#
#
# Validate example:
#
# curl -X GET 'https://id.twitch.tv/oauth2/validate' -H 'Authorization: Bearer z0i4z35akghba2z9caiq84oys3acyv'
# {
#   "client_id":"ja5swzyzsr1euwm0e53h1sxqhk553l",
#   "login":"beporter",
#   "scopes":[
#     "chat:edit",
#     "moderator:read:followers",
#     "moderator:read:moderators",
#     "user:bot"
#   ],
#   "user_id":"29519624",
#   "expires_in":5106745
# }
#
#
#
# python 3 -i brb_timer.py
#
# t = TwitchApi('ja5swzyzsr1euwm0e53h1sxqhk553l', 'z0i4z35akghba2z9caiq84oys3acyv')
# t.user()
# t.validate()
#
# DEBUG:root:https://api.twitch.tv/helix/users?login=beporter
# DEBUG:root:{}
# send: b'GET /helix/users?login=beporter HTTP/1.1\r\nAccept-Encoding: identity\r\nHost: api.twitch.tv\r\nUser-Agent: brb_timer.py\r\nAccept: application/json\r\nAuthorization: Bearer z0i4z35akghba2z9caiq84oys3acyv\r\nClient-Id: ja5swzyzsr1euwm0e53h1sxqhk553l\r\nConnection: close\r\n\r\n'
# reply: 'HTTP/1.1 200 OK\r\n'
# header: Content-Type: application/json; charset=utf-8
# header: Content-Length: 352
# header: Connection: close
# header: Date: Tue, 11 Aug 2026 19:24:02 GMT
# header: Vary: Accept-Encoding
# header: Vary: Origin
# header: Ratelimit-Limit: 800
# header: Ratelimit-Remaining: 799
# header: Ratelimit-Reset: 1786476243
# header: Timing-Allow-Origin: https://www.twitch.tv
# header: X-Cache: Miss from cloudfront
# header: Via: 1.1 a4f9034f040b2c72126eaff1ca10fb64.cloudfront.net (CloudFront)
# header: X-Amz-Cf-Pop: ORD56-P14
# header: Alt-Svc: h3=":443"; ma=86400
# header: X-Amz-Cf-Id: CseCrecuY8O0L998yikNRu84GASZuCmzMHaGw8BBg5QmC_qpbeti_Q==
# header: Strict-Transport-Security: max-age=300; includeSubDomains
# {
#   'data': [
#     {
#       'id': '29519624',
#       'login': 'beporter',
#       'display_name': 'beporter',
#       'type': '',
#       'broadcaster_type': '',
#       'description': 'Just this guy, you know?',
#       'profile_image_url': 'https://static-cdn.jtvnw.net/jtv_user_pictures/20dc4771-48f3-4ad5-b894-91d067a577cb-profile_image-300x300.png',
#       'offline_image_url': '',
#       'view_count': 0,
#       'created_at': '2012-04-05T00:36:19Z'
#     }
#   ]
# }
#
#
# {
#   'data': [
#     {
#       'id': '12989801',
#       'login': 'enns',
#       'display_name': 'Enns',
#       'type': '',
#       'broadcaster_type': 'partner',
#       'description': 'A regular fella.',
#       'profile_image_url': 'https://static-cdn.jtvnw.net/jtv_user_pictures/eb89494b-b2ca-4ace-886c-628ffbdbbdcc-profile_image-300x300.png',
#       'offline_image_url': '',
#       'view_count': 0,
#       'created_at': '2010-06-08T10:31:45Z'
#     }
#   ]
# }
#
#
#
# scopes: user:bot%20moderator:read:followers%20moderator:read:moderators%20chat:edit
#
