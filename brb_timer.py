"""
BRB Timer for OBS Studio
https://github.com/beporter/brb-timer

"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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
    logging.debug('obs module import failed.')


###########################################################################
# Constants
###########################################################################

SCRIPT_NAME = "BRB Timer"
TEXT_SOURCE_NAME = SCRIPT_NAME

# =========================================================================
# OBS Settings Defaults

DEFAULT_BOT_USERNAME = "BRBot"
DEFAULT_AUTO_HIDE_SECONDS = 120

# =========================================================================
# Chat Commands and Messages

CMD_BRB = "!brb"
CMD_BACK = "!back"
CMD_AT = "!at"

MSG_ONLY_MOD_START = f"Only mods can start a {CMD_BRB}."
MSG_ONLY_MOD_END = f"Only mods can end a {CMD_BRB} with {CMD_BACK}."
MSG_BRB_STARTED = (
    "{streamer} is taking a break! "
    "Guess how long till they're back (without going over!) "
    "Type `{CMD_AT} MM:SS` in chat!"
)
MSG_ALREADY_RUNNING = f"{CMD_BRB} is already running! Must use {CMD_BACK} first."
MSG_GUESS_ACCEPTED = "{user} guesses {guess}."
MSG_ALREADY_GUESSED = "{user} already guessed {guess}. You can't change your guess!"
MSG_NO_BRB = (f"There's no {CMD_BRB} running. A mod must use {CMD_BRB} first.")
MSG_BRB_FINISHED = ("{streamer} is back! Winner is: {winner}")

# =========================================================================
# Twitch OAuth

# BRB Timer for Chat
# by beporter@users.sourceforge.net
# https://dev.twitch.tv/console/apps/ja5swzyzsr1euwm0e53h1sxqhk553l
TWITCH_APP_CLIENT_ID = "ja5swzyzsr1euwm0e53h1sxqhk553l"

# This EXACT URI needs to be registered as the OAuth redirect URI
# for your Twitch application.
TWITCH_REDIRECT_HOST = "127.0.0.1"
TWITCH_REDIRECT_PORT = 8765 # TODO: This needs to be dynamic based on availability.
TWITCH_REDIRECT_PATH = "/oauth/callback"
TWITCH_REDIRECT_URI = (
    f"http://{TWITCH_REDIRECT_HOST}:{TWITCH_REDIRECT_PORT}{TWITCH_REDIRECT_PATH}"
)

TWITCH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token";

TWITCH_OAUTH_SCOPES = [
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

TWITCH_CALLBACK_HTML = r"""
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

    def send_raw(self, line: str):
        with self.send_lock:
            self.socket.sendall((line + "\r\n").encode("utf-8"))

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
        self.source_name = source_name
        self.text = text

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
            obs.script_log(
                "[SourceGenerator] No active scene.",
                obs.LOG_WARNING,
            )
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
    def _create_source(self):
        """
        If we're provided a text_source_name that doesn't exist in the active scene, create one and add it in using some defaults for the OBS user to subsequently tweak.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5a57/src/add_nested.py#L16
        Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/plugins/text-freetype2/text-freetype2.c#L170
        """

        with self._data_ar() as settings:
            obs.obs_data_set_string(settings, "text", self.cached_text)
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

                try:
                    source = obs.obs_source_create(uuid.uuid4(), self.text_source_name, settings, None)
                    obs.obs_save_source(source)

                    current_scene = obs.obs_frontend_get_current_scene()
                    obs.obs_scene_add(current_scene, source)
                finally:
                    obs.obs_source_release(source)
                    obs.obs_scene_release(current_scene)


        # Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/libobs/obs.h#L153
        scene_item = obs.obs_scene_find_source(scene, self.text_source_name)
        if scene_item:
            obs.obs_sceneitem_set_pos(scene_item, obs.vec2(0, 0)) # top-left TODO: Change to top-right

            obs.obs_sceneitem_set_order(scene_item, obs.OBS_ORDER_MOVE_TOP) # layer on top

            obs.obs_sceneitem_set_bounds_type(scene_item, obs.OBS_BOUNDS_SCALE_TO_WIDTH)
            obs.obs_sceneitem_set_bounds_alignment(scene_item, obs.OBS_ALIGN_CENTER)
            obs.obs_sceneitem_set_bounds(scene_item, obs.vec2(400, 100))

            # TODO: Need to create a new source for each transition. Ref: obs.OBS_SOURCE_TYPE_TRANSITION
            show_transition = obs.TODO()
            obs.obs_source_set(show_transition, TODO)

            obs.obs_sceneitem_set_transition(scene_item, True, TODO) # True = the "show" transition
            obs.obs_sceneitem_set_transition_duration(scene_item, True, 300) # ms


            obs.obs_sceneitem_set_transition(scene_item, False, TODO) # False = the "hide" transition

            obs.obs_sceneitem_release(scene_item)


                # "show_transition": {
                #     "id": "slide_transition",
                #     "versioned_id": "slide_transition",
                #     "name": "example_text_source Show Transition",
                #     "transition": {
                #         "direction": "left"
                #     },
                #     "duration": 300
                # },
                # "hide_transition": {
                #     "id": "slide_transition",
                #     "versioned_id": "slide_transition",
                #     "name": "example_text_source Hide Transition",
                #     "transition": {
                #         "direction": "right"
                #     },
                #     "duration": 300
                # },

    #######################################################################

    @staticmethod
    def create(self, source_name):
        """
        Create the source and its transitions in the current scene.

        Returns True on success, False on failure.
        """
        scene_source = None
        source = None
        show_transition = None
        hide_transition = None
        sceneitem = None

        try:
            # Get current scene
            scene_source = obs.obs_frontend_get_current_scene()
            if scene_source is None:
                self._log( "[SourceGenerator] No active scene.", obs.LOG_WARNING)
                return False

            scene = obs.obs_scene_from_source(scene_source)
            if scene is None:
                self._log("Current frontend source is not an OBS scene.")
                return False

            # Protect against create() being called directly without
            # source_exists() having been called first.
            scene_name = obs.obs_source_get_name(scene_source)
            if self.source_exists(source_name):
                self._log(
                    "Source '%s' already exists in scene '%s'; "
                    "Nothing created."
                    % (self.source_name, scene_name),
                    obs.LOG_INFO,
                )
                return False

            # Create text source.
            source = self._create_text_source()
            if source is None:
                self._log(
                    "Could not create text source '%s'. "
                    "No compatible text source type was found."
                    % self.source_name,
                )
                return False

            # obs_source_create() can make the mutate the name on collision to keep it unique.
            actual_name = obs.obs_source_get_name(source)

            # Create show transition
            show_transition = obs.obs_source_create(
                self.show_transition_type,
                "%s - Show Transition" % actual_name,
                None,
                None,
            )
            if show_transition is None:
                self._log(
                    "Could not create show transition of type '%s'."
                    % self.show_transition_type
                )
                return False

            # Create hide transition
            hide_transition = obs.obs_source_create(
                self.hide_transition_type,
                "%s - Hide Transition" % actual_name,
                None,
                None,
            )

            if hide_transition is None:
                self._log(
                    "Could not create hide transition of type '%s'."
                    % self.hide_transition_type,
                )
                return False

            # Add source to scene.
            sceneitem = obs.obs_scene_add(scene, source)
            if sceneitem is None:
                self._log(
                    "Could not add source '%s' to scene '%s'."
                    % (actual_name, scene_name),
                )
                return False

            # Attach show/hide transitions to the scene item
            obs.obs_sceneitem_set_transition(
                sceneitem,
                True, # True  = show transition
                show_transition,
            )
            obs.obs_sceneitem_set_transition(
                sceneitem,
                False, # False = hide transition
                hide_transition,
            )
            obs.obs_sceneitem_set_transition_duration(
                sceneitem,
                True,
                self.transition_duration,
            )
            obs.obs_sceneitem_set_transition_duration(
                sceneitem,
                False,
                self.transition_duration,
            )

            # Explicitly signal the sources to serialize/save their current state.
            obs.obs_source_save(source)
            obs.obs_source_save(show_transition)
            obs.obs_source_save(hide_transition)

            self._log(
                "[SourceGenerator] Created OBS source: "
                "name='%s', uuid='%s', type='%s', "
                "scene='%s', scene_uuid='%s', sceneitem_id=%s, "
                "text='%s', show_transition='%s' (%s), "
                "hide_transition='%s' (%s), duration=%dms"
                % (
                    actual_name,
                    obs.obs_source_get_uuid(source),
                    obs.obs_source_get_id(source),
                    scene_name,
                    obs.obs_source_get_uuid(scene_source),
                    obs.obs_sceneitem_get_id(sceneitem),
                    self.text,
                    obs.obs_source_get_name(show_transition),
                    obs.obs_source_get_uuid(show_transition),
                    obs.obs_source_get_name(hide_transition),
                    obs.obs_source_get_uuid(hide_transition),
                    self.transition_duration,
                ),
                obs.LOG_INFO,
            )

            return True

        except Exception as exc:
            self._log(
                "Exception while creating source '%s': %s"
                % (self.source_name, exc),
            )

            return False

        finally:
            if sceneitem is not None:
                obs.obs_sceneitem_release(sceneitem)

            if source is not None:
                obs.obs_source_release(source)

            if show_transition is not None:
                obs.obs_source_release(show_transition)

            if hide_transition is not None:
                obs.obs_source_release(hide_transition)

            if scene_source is not None:
                obs.obs_source_release(scene_source)

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    #######################################################################

    def _create_text_source(self):
        """
        Create the first text source type available in this OBS build.
        """

        settings = obs.obs_data_create()

        try:
            obs.obs_data_set_string(
                settings,
                "text",
                self.text,
            )

            for source_id in self.text_source_ids:
                # Check whether this source type exists before trying to
                # create it.
                display_name = obs.obs_source_get_display_name(source_id)

                if display_name is None:
                    continue

                source = obs.obs_source_create(
                    source_id,
                    self.source_name,
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
    def _data_ar(self, source_settings=None):
        if not source_settings:
            settings = obs.obs_data_create()
        if source_settings:
            settings = obs.obs_source_get_settings(source_settings)
        try:
            yield settings
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
        self.text_source_name = ""
        self.visible = False
        self.cached_text = ""

    #######################################################################

    def configure(self, source_name: str):
        """
        Called from script_update() whenever the user changes settings.
        """

        self.text_source_name = source_name
        if not self.exists():
            self.create_source()

    #######################################################################


    def add_random_text_source(self, scene): # TODO: Remove this example method
        """
        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference#add-scene-with-sources-to-current-scene
        """
        r = " random text # " + str(randint(0, 10))
        with self._data_ar() as settings:
            S.obs_data_set_string(settings, "text", f"random text value {r}")
            with source_create_ar("text_ft2_source", f"random text{r}", settings) as source:
                pos = S.vec2()
                pos.x = randint(0, 1920)
                pos.y = randint(0, 1080)
                scene_item = S.obs_scene_add(scene, source)
                S.obs_sceneitem_set_pos(scene_item, pos)



    ######################################################################

    def exists(self) -> bool:
        with self._get_source() as s:
            exists = (s != False)

        return exists

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

    def clear(self) -> None:
        self.state = None

    def add_guess(self, username: str, seconds: int) -> bool:
        if username not in self.state.guesses.keys:
            self.state.guesses[username] = seconds

            return True

        return False

    def find_winner(self, actual_seconds: int):
        qualified = dict(filter(lambda secs: secs <= actual_seconds, self.state.guesses.items()))
        dict(sorted(qualified.items(), key=lambda secs: secs))
        winner = qualified.keys()[-1]

        return winner


###########################################################################
# Twitch API Wrapper
###########################################################################

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


class TwitchApi:

    def __init__(self, client_id, token):
        self.client_id = client_id
        self.token = token

    #######################################################################

    def user(self, username):
        resp = self.get('helix/users', {'login': username})

        return resp

    #######################################################################

    def validate(self):
        resp = self.get('oauth2/validate')

        return resp



        # extra = { "Client-Id": client_id, }

        # if response is 401 ...
        # if response['login'] is not None ...

        # if response['scopes'] not include [
        #   #EventSub
        #   channel:bot, moderator:read:moderators, user:bot, user:read:chat, user:write:chat,
        #   # IRC
        #   chat:edit, chat:read,
        #   # ??
        #   moderation:read, moderator:read:chatters]

        # username = response['login']  # (username)
        # user_id = response['user_id']  # (user_id)
        # client_id = "YOUR_CLIENT_ID"
        # user_id = "USER_ID_FROM_VALIDATE"

    #######################################################################

    def channel(self, broadcaster_id):
        raise NotImplementedError

        req = self.get('helix/channels', {'broadcaster_id': broadcaster_id}) # https://api.twitch.tv/helix/streams?user_login=$user&type=live&first=1

    #######################################################################

    def moderators(self):
        raise NotImplementedError

        # 1. Is the user currently streaming?
        streams = self.get("streams", {"user_id": user_id})["data"]

        # https://api.twitch.tv/helix/moderation/moderators?broadcaster_id=$broadcaster_id&first=20
        # response[][user_id], response[][user_login]
        if not streams:
            print("Not live")
        else:
            get(
                "moderation/moderators",
                {"broadcaster_id": user_id, "first": 100},
            )["data"]

            stream = streams[0]

            print("Live:", True)
            print("Channel:", stream["user_login"])
            print("Display name:", stream["user_name"])

            # 2. Get the channel's moderators
            moderators = twitch_get(
                "moderation/moderators",
                {"broadcaster_id": user_id, "first": 100},
            )["data"]

            print("Moderators:")
            for mod in moderators:
                print(" ", mod["user_login"])

    #######################################################################

    def get(self, path, params = {}, extra_headers = {}):
        server = self.server(path)
        url = f"{server}/{path}?" + urllib.parse.urlencode(params)
        logging.debug(url)
        logging.debug(extra_headers)
        req = urllib.request.Request(url, headers=self.headers(extra_headers))

        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                try:
                    detail = json.load(e)["message"]
                except Exception:
                    detail = "unauthorized"

#                 raise RuntimeError(f"Twitch: {detail}") from e
#
#             raise
            logging.debug(e)
        return False

    #######################################################################

    def server(self, path):
        match path.split('/', 2)[0]:
            case 'oauth2':
                return 'https://id.twitch.tv'
            case _:
                return 'https://api.twitch.tv'

    #######################################################################

    def headers(self, extra = {}):
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
        self.callback = callback

    #######################################################################

    def setServer(self, server) -> None:
        """
        This non-standard hook lets us shut our own parent http server down after successful POST processing.
        """
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
        if parsed.path != TWITCH_REDIRECT_PATH:
            self.respond(404, "text/html; charset=utf-8", "<h1>Not found.</h1>")

            return

        self.respond(200, "text/html; charset=utf-8", TWITCH_CALLBACK_HTML.encode("utf-8"))

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
        # =================================================================
        # Runtime state
        # =================================================================

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
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(OAUTH_SCOPES),
            "state": _oauth_state,
        }

        authorization_url = (
            TWITCH_AUTHORIZE_URL
            + "?"
            + urllib.parse.urlencode(params)
        )

        print(
            "[Twitch OAuth] Opening Twitch authorization..."
        )

        webbrowser.open(authorization_url)


    def run_server(self):
        try:
            self.server = TwitchOAuthServer()
            self.server_ready.set()

            print(
                "[Twitch OAuth] Listening on:",
                REDIRECT_URI
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
            (TWITCH_REDIRECT_HOST, TWITCH_REDIRECT_PORT),
            TwitchOAuthHandler
        )

    #######################################################################

    @staticmethod
    def _log(self, message, level = obs.LOG_ERROR):
        obs.script_log(level, f"[{self.__class__.__name__}] {message}")






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
            |          v        |                |               |
            |      TwitchApi    |                |               |
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
            * provide default property values.
        * script_description() called
            * provide static string.
        * script_load() called
            * check for existing twitch oauth creds
        * script_update() called once
            * saves any new settings.

    * User opens the script's Properties pane in the GUI ->
        * script_properties() called
            * construct script properties for display in GUI.
        * whenever a property value is changed ->
            * script_update() called.

    * OBS_FRONTEND_EVENT_FINISHED_LOADING event fires ->
        * handler:
            * creates text source if it doesn't exist.
            * registers event handlers to:
                * respond to OBS_FRONTEND_EVENT_FINISHED_LOADING
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
        self.state = BRBState()
        self.guesses = GuessManager(self.state)
        self.renderer = TimerRenderer()
        self.irc: Optional[TwitchIRCClient] = None
        self.server: Optional[TwitchOAuthServer] = None

    #######################################################################
    # Lifecycle
    #######################################################################

    def start(self):
        config = obs.obs_frontend_get_profile_config()
        token = obs.config_get_string(config, "Twitch", "Token")

        if token is None:
            self._log('OBS not logged into Twitch.', obs.INFO)
            return

        api = TwitchApi(TWITCH_APP_CLIENT_ID, token)
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

    def command_brb(self, username: str):
        """
        Handler that's called when self.irc returns a ChatMessage with a !brb commands.
        """
        raise NotImplementedError

    #######################################################################

    def command_back(self, username: str):
        """
        Handler that's called when self.irc returns a ChatMessage with a !back commands.
        """
        raise NotImplementedError

    #######################################################################

    def command_at(self, username: str, guess: str):
        """
        Handler that's called when self.irc returns a ChatMessage with an !at commands.
        """
        raise NotImplementedError

    #######################################################################
    # OBS
    #######################################################################

    def tick(self):
        raise NotImplementedError

    #######################################################################

    def twitch_channel(self):
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
    # Internal Helpers
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

def script_description():
    return (
        "BRB Timer\n\n"
        "<a href=\"https://github.com/beporter/brb-timer\">github.com/beporter/brb-timer</a>\n\n"
        "Let chatters guess when the streamer will return from being AFK.\n\n"
        f"Mods can use {CMD_BRB} to start the on-screen timer and allow guessing.\n"
        f"Chatters can use <code>{CMD_AT} MM:SS</code> to enter a single gueess.\n"
        "Chatters can't guess multiple times because they could just keep updating their guess as the timer gets higher and higher.",
        f"Mods can end the guessing when the streamer returns with {CMD_BACK}.\n"
        "The closest without going over will be displayed as winner in the chat.\n"
    )

###########################################################################

def script_properties():

    props = obs.obs_properties_create()

    if script.twitch_creds_expired():
        obs.obs_properties_add_button(
            props,
            "connect_twitch_button"
            "Connect Twitch",
            script.twitch_creds_reconnect()
        )
    elif script.twitch_creds_present():
        obs.obs_properties_add_button(
            props,
            "disconnect_twitch_button"
            "Disconnect Twitch",
            script.twitch_creds_disconnect()
        )
    else:
        obs.obs_properties_add_button(
            props,
            "connect_twitch_button"
            "Connect Twitch",
            script.twitch_creds_connect()
        )

    obs.obs_properties_add_text(
        props,
        "channel_name",
        "Channel Name",
        obs.OBS_TEXT_DEFAULT,
    )

    obs.obs_properties_add_text(
        props,
        "bot_username",
        "Bot Username",
        obs.OBS_TEXT_DEFAULT,
    )

    # TODO: If we're auto-creating, maybe this should either be a button, or a dropdown list?
    obs.obs_properties_add_text(
        props,
        "text_source",
        "Text Source",
        obs.OBS_TEXT_DEFAULT,
    )

    obs.obs_properties_add_int(
        props,
        "auto_hide_delay",
        "Auto-hide Delay (seconds)",
        1,
        3600,
        1,
    )

    obs.obs_properties_add_text(
        props,
        "client_id",
        "OBS's Twitch OAuth Client ID",
        obs.OBS_TEXT_DEFAULT,
    )

    return props

###########################################################################

def script_defaults(settings):
    obs.obs_properties_add_button(
        props,
        "oauth_starter",
        "Connect Twitch",
        oauth_starter
    )

    # TODO: Default to TwitchApi.channel() value, when available.
    obs.obs_data_set_default_string(
        settings,
        "channel_name",
        script.twitch_channel(),
    )

    obs.obs_data_set_default_string(
        settings,
        "bot_username",
        DEFAULT_BOT_USERNAME,
    )

    # (Can't set a default text_source)
    # TODO: Maybe **create** a text source in the active scene?

    obs.obs_data_set_default_int(
        settings,
        "auto_hide_delay",
        DEFAULT_AUTO_HIDE_SECONDS,
    )

    obs.obs_data_set_default_string(
        settings,
        "client_id",
        DEFAULT_CLIENT_ID,
    )

###########################################################################

def script_update(settings):

    script.renderer.configure(

        obs.obs_data_get_string(
            settings,
            "text_source",
        )

    )

###########################################################################

def script_load(settings):
    """
    Called once when the script is loaded.
    """
    global _oauth_result

    access_token = obs.obs_data_get_string(
        settings,
        "twitch_access_token"
    )

    token_type = obs.obs_data_get_string(
        settings,
        "twitch_token_type"
    )

    scopes = obs.obs_data_get_string(
        settings,
        "twitch_scopes"
    )

    if access_token:
        _oauth_result = {
            "access_token": access_token,
            "token_type": token_type,
            "scope": scopes,
        }

        print(
            "[Twitch OAuth] Existing token loaded."
        )

    # Auto-create the text source in the current scene when not already present.
    generator = SourceGenerator(
        source_name=TEXT_SOURCE_NAME,
        text="",
    )

    if generator.source_exists():
        obs.script_log(
            obs.LOG_INFO,
            f"[{SCRIPT_NAME}] Timer Source '%s' is already present in the current scene."
            % TEXT_SOURCE_NAME
        )
    else:
        generator.create()

    obs.script_log(obs.LOG_INFO, f"{SCRIPT_NAME} loaded.")

###########################################################################

def script_save(settings):
    """
    Called before OBS saves the script settings.
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
