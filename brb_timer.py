"""
BRB Timer for OBS Studio
https://github.com/beporter/brb-timer

"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import http.client as http_client
import http.server
import inspect
import json
import logging
import secrets
import socket
import ssl
import sys
from textwrap import dedent
import threading
import time
from typing import Callable, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request
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
    obs.script_log = lambda _lvl, msg: print(msg)

    logging.debug('obs module unavailable. Replaced with a mock.')


###########################################################################
# Constants
###########################################################################

SCRIPT_NAME = "BRB Timer"
SCRIPT_VERSION = 1.0

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


###########################################################################
# Guessing State Storage
###########################################################################

class BRBState:
    """
    Holds the current BRB session state.

    That includes whether a !brb is running, what chatters have guessed,
    and who the most recent winner is.

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
        | auto-hide timer expires TODO: Need to set this up.
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

    #######################################################################
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
    def from_data(self, data):
        # Ref: https://docs.obsproject.com/reference-settings#get-functions
        # This is dumb, but it's easier for python to parse json than
        # OBS's `obs_data_t` objects.
        parsed = OBS.data_get_json(data)
        attrs = [attr for attr in vars(self) if not name.startswith("__")]
        for name, value in parsed.items():
            # TODO: This might not work for merging. Could overwrite a valid value with None.
            if name in attrs:
                setattr(self, name, value)

    #######################################################################
    def to_data(self, existing = None):
        with existing if existing is not None else OBS.data() as data:
            for name in dir(self):
                val = getattr(self, name)
                OBS.data_set(name, val)

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
    """
    Message passing container from the IRC client to the command parser.
    """
    username: str
    display_name: str
    message: str
    is_mod: bool
    is_broadcaster: bool


###########################################################################
# OBS Module Wrapper
###########################################################################

class OBS:
    """
    Minimal static wrapper around the `obs` module, to keep direct
    calls out of the rest of the script.

    Beside the OBS hook functions way down at the bottom of the file,
    this class should be the only place that calls the `obs.*` module
    directly.

    Any method that returns something that needs to be released later
    should be used with `with OBS.thing() as thing:`
    """

    # ---------------------------------------------------------------------
    # Source
    # ---------------------------------------------------------------------

    #######################################################################
    @contextmanager
    @classmethod
    def source_by_name(self, name: str):
        """
        Yield the source identified by the provided name, if possible.
        """
        try:
            source = obs.obs_get_source_by_name(name)
            if source is None:
                return False

            yield source

        finally:
            obs.obs_source_release(source)

    #######################################################################
    @contextmanager
    @classmethod
    def source_by_uuid(self, uuid: str):
        """
        Yield the source identified by the provided uuid, if possible.
        """
        try:
            source = obs.obs_get_source_by_uuid(uuid)
            if source is None:
                return False

            yield source

        finally:
            obs.obs_source_release(source)

    #######################################################################
    @contextmanager
    @classmethod
    def source_name_in_scene(self, source_name: str, scene = None):
        """
        Yield the source from the provided scene, if present.

        If no scene is passed, we use the currently active scene.
        """
        with scene if scene is not None else self.scene_current() as scene:
            try:
                # TODO: Might need to switch to the `_recursive` variant.
                source = obs.obs_scene_find_source(scene, source_name)

                yield source

            finally:
                obs.obs_source_release(source)

    #######################################################################
    @contextmanager
    @classmethod
    def source_create(self):
        """
        Yield a brand new source.
        """
        try:
            source = obs.obs_source_create()

            yield source

        finally:
            obs.obs_source_release(source)

    #######################################################################
    @classmethod
    def source_create_text(
        self,
        text: str = "",
        source_type_id: str = None,
    ):
        """
        Create and return the first text source type available in the
        current OBS build.
        """
        # OBS uses different built-in text source IDs on different platforms.
        if source_type_id is None:
            if sys.platform.startswith("win"):
                source_types = [
                    "text_gdiplus_v2",
                    "text_gdiplus",
                ]
            else:
                source_types = [
                    "text_ft2_source_v2",
                    "text_ft2_source",
                ]
        else:
            source_types = [source_type_id]

        # Import default text settings.
        with self.text_settings(text) as settings:
            # Check if the source type exists before trying to create the source.
            for source_id in source_types:
                display_name = obs.obs_source_get_display_name(source_id)

                if display_name is None:
                    continue

                source = obs.obs_source_create(
                    source_id,
                    self.text_source_name,
                    settings,
                    None,
                )

                # Return the first successfully created text source.
                if source is not None:
                    return source

            # TODO: Add logging
            return None

    #######################################################################
    @contextmanager
    @classmethod
    def source_update(self, source, changes: Dict[str, any]):
        """
        Apply settings changes to the provided source.
        """
        with self.data_set_all(changes) as new_settings:
            obs.obs_source_update(source, new_settings)

    #######################################################################
    @classmethod
    def source_save(self, source):
        """
        Save the provided source to OBS's persistent storage.
        """
        return obs.obs_save_source(source)

    #######################################################################
    @classmethod
    def source_type(self, source) -> str:
        """
        Get the "ID" (which is really a 'source type id') for the
        provided source.
        """
        return obs.obs_source_get_id(source)

    #######################################################################
    @classmethod
    def source_name(self, source) -> str:
        """
        Get the display name of the provided source.
        """
        return obs.obs_get_source_name(source)

    #######################################################################
    @classmethod
    def source_uuid(self, source) -> str:
        """
        Get the display name of the provided source.
        """
        return obs.obs_get_source_uuid(source)

    #######################################################################
    @classmethod
    def source_text(self, source) -> str:
        """
        Get the on-screen text for the provided source.

        Behavior is currently undefined if the source isn't a "text"
        source.
        """
        with self.data(obs.obs_source_get_settings(source)) as settings:
            text = obs.obs_data_get_string(settings, "text")

        return text


    # ---------------------------------------------------------------------
    # Scene
    # ---------------------------------------------------------------------

    #######################################################################
    @contextmanager
    @classmethod
    def scene_current(self):
        """
        Yields an obs_scene object and auto-releases afterward.

        Usage example:

            with OBS.scene_current() as scene:
                # do something with `scene`.
        """
        scene_source = obs.obs_frontend_get_current_scene()
        if scene_source is None:
            OBS.warn("No active scene.")

            return False

        try:
            scene = obs.obs_scene_from_source(scene_source)
            if scene is None:
                return False

            yield scene

        finally:
            obs.obs_source_release(scene_source)
            obs.obs_scene_release(scene)

    #######################################################################
    @classmethod
    def scene_name(self, scene = None) -> str:
        """
        Get the display name of the provided scene.

        Or the currently active scene if none is provided.
        """
        with scene if scene is not None else self.scene_current() as scene:
            return obs.obs_get_scene_name(scene)

    #######################################################################
    @classmethod
    def scene_active(self, scene) -> bool:
        """
        Returns true when the provided scene is the currently active scene.
        """
        with self.scene_current() as current:
            same_scene = (OBS.scene_name(current) == OBS.scene_name(scene))

        return same_scene

    # ---------------------------------------------------------------------
    # Scene Item
    # ---------------------------------------------------------------------

    #######################################################################
    @contextmanager
    @classmethod
    def scene_add(self, source, scene = None):
        """
        Yields a sceneitem object resulting from adding the provided
        source to the provided scene.

        Uses the currently active scene if none is provided.
        """
        with scene if scene is not None else self.scene_current() as current:
            sceneitem = obs.obs_scene_add(current, source)

            yield sceneitem

            return sceneitem

    #######################################################################
    @contextmanager
    @classmethod
    def sceneitem_by_name(self, name: str, scene = None):
        """
        Yields a sceneitem object matching the provided name. If no scene
        is provided, the currently active scene will be used for the
        search.
        """
        with scene if scene is not None else self.scene_current() as current:
            # TODO: Might need to use the recursive version of this method.
            sceneitem = obs.obs_scene_find_source(current, name)

            yield sceneitem

            return sceneitem

    #######################################################################
    @classmethod
    def sceneitem_set_visible(self, sceneitem, show: bool):
        """
        Tells OBS to show or hide the provided sceneitem on-screen.

        Ref: https://github.com/BraatheSaaS/OBS-Studio-Python-Scripting-Cheatsheet-obspython-Examples-of-API#toggle-sceneitem-visibility
        """
        return obs.obs_sceneitem_set_visible(sceneitem, show)

    #######################################################################
    @classmethod
    def sceneitem_visible(self, sceneitem) -> bool:
        """
        Returnes true if the provided sceneitem is currently visible
        on-screen.
        """
        return obs.obs_sceneitem_visible(sceneitem)

    #######################################################################
    @classmethod
    def sceneitem_id(self, sceneitem) -> str:
        """
        Returns the OBS ID of the provided sceneitem.
        """
        return obs.obs_sceneitem_get_id(sceneitem)

    # ---------------------------------------------------------------------
    # Transition
    # ---------------------------------------------------------------------

    #######################################################################
    @classmethod
    def transition_source_create(
        self,
        sceneitem,
        source_name: str,
        transition_type: str,
        visibility: str,
        direction: str,
        target_uuid: str,
    ) -> str|False:
        """
        Creates a new text-type Source object with the provided settings.

        Returns False if any part of the process fails.
        """
        # Input validation.
        if visibility not in ("show", "hide"):
            raise ValueError("visibility must be 'show' or 'hide'")

        if direction not in ("left", "right"):
            raise ValueError("direction must be 'left' or 'right'")

        # Create the source containing the transition.
        with self._data_ar() as settings:
            obs.obs_data_set_string(settings, "direction", direction)
            transition = obs.obs_source_create(
                transition_type,
                source_name,
                settings,
                None,
            )

        if transition is None:
            OBS.debug(f"Failed to create {visibility} slide_transition source.")
            return False

        # Find the scene_item for the timer text source.
        with self.current_scene() as scene:
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
            OBS.debug(f"Source UUID {target_uuid!r} is not present in the current scene.")
            return False

        try:
            # Attach the transition to the scene item and set duration.
            obs.obs_sceneitem_set_transition(
                target_item, visibility == "show",
                transition,
            )
            obs.obs_sceneitem_set_transition_duration(
                target_item,
                visibility == "show",
                self.TRANSITION_DURATION_MS,
            )

            # Set on-screen position to top-right.
            vi = obs.obs_video_info()
            obs.obs_get_video_info(vi)
            obs.obs_sceneitem_set_alignment(
                sceneitem,
                obs.OBS_ALIGN_RIGHT | obs.OBS_ALIGN_TOP,
            )
            obs.obs_sceneitem_set_pos(
                sceneitem,
                obs.vec2(vi.base_width, 0),
            )

            # Set z-index to top.
            obs.obs_sceneitem_set_order(
                sceneitem,
                obs.OBS_ORDER_MOVE_TOP,
            ) # layer on top

            # Set item bounds.
            # obs.obs_sceneitem_set_bounds_type(sceneitem, obs.OBS_BOUNDS_SCALE_TO_WIDTH)
            # obs.obs_sceneitem_set_bounds_alignment(sceneitem, obs.OBS_ALIGN_CENTER)
            # obs.obs_sceneitem_set_bounds(sceneitem, obs.vec2(400, 100))

            uuid = obs.obs_source_get_uuid(transition)

        finally:
            obs.obs_source_release(transition)

        return uuid

    #######################################################################
    @classmethod
    def transition_duration(self, transition_sceneitem, visibility: str):
        try:
            duration = obs.obs_sceneitem_get_transition_duration(
                transition_sceneitem,
                visibility == "show",
            )
        finally:
            obs.obs_sceneitem_release(transition_sceneitem)

        return duration

    #######################################################################
    @classmethod
    def transition_target(self, transition_sceneitem, visibility: str):
        target = obs.obs_sceneitem_get_transition(
            transition_sceneitem,
            visibility == "show",
        )

        return self.source_uuid(target)

    # ---------------------------------------------------------------------
    # Data
    # ---------------------------------------------------------------------

    #######################################################################
    @contextmanager
    @classmethod
    def text_settings(
        self,
        text: str,
        width: int = None,
        wrap: bool = False,
        outline: bool = True,
        drop_shadow: bool = False,
        color_top: int = 0xffffff00, # Format: 0xrrggbbaa
        color_bottom: int = 0xffffff00,
        font_face: str = "Monaco",
        font_style: str = "Regular",
        font_size_px: int = 256,
    ):
        """
        Helper that yields an obs_data object pre-configured with
        our stock text Source settings.

        Usage:

            with OBS.text_settings() as settings:
                obs.obs_source_create(id, name, settings, None)
        """
        settings = self.data()

        try:
            obs.obs_data_set_string(settings, "text", text)
            obs.obs_data_set_bool(settings, "word_wrap", wrap)
            if width is not None:
                obs.obs_data_set_int(settings, "custom_width", width)

            obs.obs_data_set_bool(settings, "outline", outline)
            obs.obs_data_set_bool(settings, "drop_shadow", drop_shadow)
            # Ref: https://docs.obsproject.com/reference-properties#c.obs_properties_add_color_alpha
            obs.obs_data_set_int(settings, "color1", color_top)
            obs.obs_data_set_int(settings, "color2", color_bottom)

            with self.data() as font:
                obs.obs_data_set_string(font, "face", font_face)
                obs.obs_data_set_string(font, "style", font_style)
                obs.obs_data_set_int(font, "size", font_size_px)
                obs.obs_data_set_int(font, "flags", 0)

                obs.obs_data_set_obj(settings, "font", font)

            yield settings

        finally:
            obs.obs_data_release(settings)

    #######################################################################
    @contextmanager
    @classmethod
    def data(self, source_settings = None):
        """
        Helper that yields an obs_data object either from the
        passed settings, or from scratch. Auto-releases after use.

        Usage example:

        with OBS.data() as d:
            # do something with `d`.
        """
        if source_settings:
            data = obs.obs_source_get_settings(source_settings)
        else:
            data = obs.obs_data_create()

        if data is None:
            return False

        try:
            yield data

        finally:
            obs.obs_data_release(data)

    #######################################################################
    @classmethod
    def data_get_json(self, data):
        return json.load(obs.obs_data_get_json(data))

    #######################################################################
    @contextmanager
    @classmethod
    def data_set_all(self, changes):
        """
        Currently only handles scalar values.
        """
        with self.data() as new_settings:
            for (k, v) in changes.items():
                self.data_set(new_settings, k, v)

            yield new_settings

    #######################################################################
    @classmethod
    def data_set(data, key: str, val: any, **args):
        """
        Attempts to call the appropriate obs_data_set_*()` method by
        examining the python type of the supplied value.
        """
        match type(val).__name__:
            case 'int':
                OBS.data_set_int(data, key, val, **args)
            case 'str':
                OBS.data_set_string(data, key, val)
            case _:
                OBS.data_set_string(data, key, val)

    #######################################################################
    @classmethod
    def data_set_string(self, data, key: str, val: str):
        obs.obs_data_set_string(data, key, val)

    #######################################################################
    @classmethod
    def data_set_int(
        self,
        data,
        key: str,
        val: int,
        min: int = None,
        max: int = None,
        step: int = None
    ):
        obs.obs_data_set_int(data, key, val, min, max, step)


    # ---------------------------------------------------------------------
    # Events
    # ---------------------------------------------------------------------

    #######################################################################
    @classmethod
    def event_register_router(self):
        """
        This method is the public interface for registering our wrapper's
        event router method (below).

        Registering the router will reset any existing listeners.
        """
        obs.obs_frontend_add_event_callback(self.event_router)
        self._events = {}

        # Register additional signal handlers for specific objects+events
        # Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/scene_sig_con.py#L23
        # sh = obs.obs_source_get_signal_handler(source)
        # obs.signal_handler_connect(sh, "item_add", callback)
        # obs.obs_source_release(source)

    #######################################################################
    @classmethod
    def event_router(self, event: int):
        """
        After OBS.event_register_router() has been called, this method
        is invoked by OBS whenever an event is fired.

        This router is only responsible for determining whether to
        respond to an event (and triggering the callback, if so), or
        to ignore it.
        """
        if event in self._event.keys():
            return self._event[event]()

    #######################################################################
    @classmethod
    def event_add(self, event: int, callback: Callable):
        """
        Register a callback for a specific OBS event.

        The calling context MUST have already called
        OBS.event_register_router()
        """
        self._events[event] += callback
        self.debug(f"Event listener registered: {callback.__name__}")


    # ---------------------------------------------------------------------
    # Logging
    # ---------------------------------------------------------------------

    #######################################################################
    @classmethod
    def error(self, msg: str):
        self._log(msg, obs.LOG_ERROR)

    #######################################################################
    @classmethod
    def warn(self, msg: str):
        self._log(msg, obs.LOG_WARNING)

    #######################################################################
    @classmethod
    def info(self, msg: str):
        self._log(msg, obs.LOG_INFO)

    #######################################################################
    @classmethod
    def debug(self, msg: str):
        self._log(msg, obs.LOG_DEBUG)

    #######################################################################
    @classmethod
    def log_source(self, source):
        msg = "<Source name='%s', uuid='%s', type='%s' contents='%s'>\n" % (
            self.source_name(source),
            self.source_uuid(source),
            self.source_type(source),
            self.source_text(source),
        )
        self._log(msg, obs.LOG_DEBUG)

    #######################################################################
    @classmethod
    def log_scene(self, scene, sceneitem):
        msg = "<%sScene name='%s', sceneitem_id='%s'>\n" % (
            "Active " if self.scene_active(scene) else "",
            self.scene_name(scene),
            self.sceneitem_id(sceneitem),
        )
        self._log(msg, obs.LOG_DEBUG)

    #######################################################################
    @classmethod
    def log_transition(self, transition_source, transition_sceneitem):
        with self.transition_source(transition_sceneitem, True) as show_source:
            if transition_source == show_source:
                visibility = "show"
            else:
                visibility = "hide"

        msg = "<Transition name='%s' uuid='%s' type='%s' visibility='%s' duration='%s' target='%s'>\n" % (
            self.source_name(transition_source),
            self.source_uuid(transition_source),
            self.source_type(transition_source),

            visibility,
            self.transition_duration(transition_sceneitem, visibility),
            self.transition_target(transition_sceneitem, visibility),
        )
        self._log(msg, obs.LOG_DEBUG)

    #######################################################################
    @classmethod
    def _log(self, msg: str, level: int):
        # Grab our frame and at most three parent frames.
        stack = inspect.stack(0)[0:3]

        # The last element in the slice of 3 is the farthest-most caller.
        # Handles the case where there's less than 2 stacks above us.
        callers_caller_frameinfo = stack[-1]

        # Dig into the frame details to get the fully qualified method name.
        calling_method = callers_caller_frameinfo.frame.f_code.co_qualname

        obs.script_log(level, f"[{calling_method}] {msg}")

# TODO: Remove
# Wrapper.called_method() -> Wrapper._log() -> OBS.debug() -> OBS._log()
# class Wrapper:
#     @classmethod
#     def called_method(self):
#         self._log('important message')
#     @classmethod
#     def _log(self, msg):
#         print("sending: " + msg)
#         OBS.debug(msg)

###########################################################################
# Twitch IRC Client
###########################################################################

class TwitchIRCClient:
    """
    Although Twitch now recommends EventSub for many integrations, chat
    over IRC is still supported and is perfectly adequate for this project.

    The IRC client should have one responsibility:

    socket
        ↓
    IRC parsing
        ↓
    ChatMessage objects
        ↓
    CommandParser

    It knows nothing about `!brb`, `!at`, timers, guesses, etc. and
    encodes its message parsing into ChatMessage payloads to keep raw
    IRC lines out of the rest of the app.
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
            target=self._receive_loop,
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
    def send_chat(self, message: str):
        self._send_raw(f"PRIVMSG #{self.channel} :{message}")

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #######################################################################
    def _receive_loop(self):
        while self.running:
            try:
                self._connect()
                while self.running:
                    line = self.reader.readline()
                    if not line:
                        raise ConnectionError("EOF")

                    self._handle_line(line.rstrip())

            except Exception as ex:
                OBS.warn(f"TwitchIRC: {ex}")
                try:
                    if self.socket:
                        self.socket.close()
                except Exception:
                    pass

                time.sleep(self.RECONNECT_DELAY)

    #######################################################################
    def _connect(self):
        raw = socket.create_connection((self.HOST, self.PORT), timeout=15)
        ctx = ssl.create_default_context()
        self.socket = ctx.wrap_socket(raw, server_hostname=self.HOST)
        self.reader = self.socket.makefile(
            "r",
            encoding="utf-8",
            newline="\r\n",
        )

        self._send_raw(f"PASS oauth:{self.oauth}")
        self._send_raw(f"NICK {self.username}")

        # Ref: https://dev.twitch.tv/docs/chat/irc
        self._send_raw("CAP REQ :twitch.tv/tags")
        self._send_raw("CAP REQ :twitch.tv/commands")

        self._send_raw(f"JOIN #{self.channel}")

    #######################################################################
    def _handle_line(self, line: str):
        if line.startswith("PING"):
            self._send_raw(line.replace("PING", "PONG", 1))

            return

        if " PRIVMSG " not in line:
            return

        msg = self._parse_privmsg(line)
        if msg:
            self.callback(ChatMessage(msg))

    #######################################################################
    def _parse_privmsg(self, line: str) -> Optional[ChatMessage]:
        try:
            tags_raw, remainder = line.split(" ", 1)
            tags = self._parse_tags(tags_raw[1:])
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
    def _send_raw(self, line: str):
        with self.send_lock:
            self.socket.sendall((line + "\r\n").encode("utf-8"))

    #######################################################################
    @staticmethod
    def _parse_tags(raw: str) -> Dict[str, str]:
        result = {}

        for field in raw.split(";"):
            if "=" in field:
                k, v = field.split("=", 1)
                result[k] = v

        return result


###########################################################################
# Source Generator (Text Source with show/hide scene_item transitions)
###########################################################################

class SourceGenerator:
    """
    Creates an OBS text source in the currently active OBS scene if it
    does not already exist.
    """

    SHOW_TRANSITION_TYPE = "slide_transition"
    HIDE_TRANSITION_TYPE = "slide_transition"
    SHOW_TRANSITION_DIR = "left"
    HIDE_TRANSITION_DIR = "right"
    TRANSITION_DURATION_MS = 300

    def __init__(
        self,
        source_name,
        text = "hello world",
        text_source_id = None,
    ):
        self.text_source_name = source_name
        self.text = text
        self.text_source_id = text_source_id

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
        return OBS.source_name_in_scene(source_name) is not None

    #######################################################################
    @contextmanager
    def create_source(self):
        """
        If we were provided a text_source_name that doesn't exist in the
        active scene, create one and add it into the active scene using
        some defaults that the OBS user can subsequently tweak.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5a57/src/add_nested.py#L16
        Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/plugins/text-freetype2/text-freetype2.c#L170
        """
        if self.source_exists(self.text_source_name):
            OBS.info(
                "Source '%s' already exists in scene '%s'; "
                "Nothing created."
                % (self.source_name, OBS.scene_name()),
            )
            return False

        # Create the text source for the timer display
        with OBS.source_create_text(self.text, self.text_source_id) as source:
            OBS.source_save(source)
            text_source_uuid = OBS.source_uuid(source)

            # Add source to scene.
            # Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/libobs/obs.h#L153
            with OBS.scene_current() as scene:
                if scene is None:
                    OBS.debug("Current frontend source is not an OBS scene.")
                    return False

                scene_name = OBS.scene_name(scene)

                # Add transitions to scene item.
                with OBS.scene_add(source, scene) as sceneitem:
                    if sceneitem is None:
                        OBS.debug(
                            f"Could not add source '{self.text_source_name}' to scene '{scene_name}'.",
                        )
                        return False

                    show_uuid = OBS.transition_source_create(
                        sceneitem,
                        SOURCES.TRANSITION_SHOW,
                        "slide_transition",
                        "show",
                        "left",
                        text_source_uuid,
                    )
                    hide_uuid = OBS.transition_source_create(
                        sceneitem,
                        SOURCES.TRANSITION_HIDE,
                        "slide_transition",
                        "hide",
                        "right",
                        text_source_uuid,
                    )

                    OBS.debug("Created OBS source:")
                    OBS.log_source(source)
                    OBS.log_scene(scene)
                    OBS.log_transition(OBS.source_by_uuid(show_uuid), sceneitem)
                    OBS.log_transition(OBS.source_by_uuid(hide_uuid), sceneitem)

        return True


###########################################################################
# Timer Renderer
###########################################################################

class TimerRenderer:
    """
    This class handles the OBS display of the on-screen timer.

    If the targeted text source doesn't already exist in the active
    scene, it'll be generated.
    """

    #######################################################################
    def __init__(self, text_source_name: str, init_text: str):
        self.text_source_name = text_source_name
        self.source_uuid: str = None

        # Auto-create the text source in the current scene when not
        # already present.
        if SourceGenerator.source_exists(text_source_name):
            OBS.info(
                f"Timer Text Source '{text_source_name}' is already present in the current scene.",
            )

            return

        generator = SourceGenerator(
            source_name = text_source_name,
            text = init_text,
        )
        if generator.create():
            OBS.info(
                f"Created new Timer Text Source '{text_source_name}' successfully.",
            )
        else:
            OBS.debug(
                f"Failed to create new Timer Text Source '{text_source_name}'.",
            )

    #######################################################################
    def set_text(self, text: str) -> None:
        """
        Update the on-screen timer with the provided text.
        """
        source = OBS.source_by_name(self.text_source_name)
        changes = {
            'text': text,
        }
        OBS.source_update(source, changes)

    #######################################################################
    def clear(self) -> None:
        """
        Reset the on-screen timer text source.
        """
        self.set_text("")
        self.hide()

    #######################################################################
    def show(self) -> None:
        self._set_visibility(True)

    #######################################################################
    def hide(self) -> None:
        self._set_visibility(False)

    #######################################################################
    def toggle(self) -> bool:
        """
        Switch the on-screen timer on or off.

        Returns the new state. (True = visible, False = hidden)
        """
        toggled = not self._get_visibility()
        self._set_visibility(toggled)

        return toggled

    # ---------------------------------------------------------------------
    # Internal OBS helpers
    # ---------------------------------------------------------------------

    #######################################################################
    def _get_visibility(self):
        """
        Get the current visibility of the sceneitem associated with the
        configured text source.
        """
        with self._scene_item() as si:
            return OBS.sceneitem_visible(si)

    #######################################################################
    def _set_visibility(self, visibility: bool):
        """
        Set the visibility of the sceneitem associated with the
        configured text source.
        """
        with self._scene_item() as si:
            OBS.sceneitem_set_visible(si, visibility)

    #######################################################################
    def _scene_item(self):
        """
        Yields the scene item associated with the configured text source.
        """
        if not self.text_source_name:
            return False

        yield OBS.sceneitem_by_name(self.text_source_name)

    #######################################################################
    def _text_source(self):
        """
        Yields the configured text source.
        """
        if not self.text_source_name:
            return False

        yield OBS.source_by_name(self.text_source_name)


###########################################################################
# Guess Manager
###########################################################################

class GuessManager:
    """
    Manages updates to any active brb guessing.

    manager = GuessManager(BRBState())
    manager->start()                      # Allow guesses.
    manager->add_guess(username, secs)
    manager->add_guess(diff_user, diff_secs)
    manager->end()                        # Block guesses.
    manager->winner()                     # Return last winner, till reset.
    manager->clear()                      # Reset back to defaults.
    """

    #######################################################################
    def __init__(self, state: BRBState):
        self.state = state
        self.actual_secs = None

    #######################################################################
    def clear(self) -> None:
        self.actual_secs: int = None
        self.state.reset()

    #######################################################################
    def start(self) -> None:
        self.state.last_result = self.winner()

        self.actual_secs = None

        self.state.active = True
        self.state.start_time = time.time()
        self.state.stop_time = None
        self.state.guesses = {}
        self.state.winner = None

    #######################################################################
    def add_guess(self, username: str, seconds: int) -> bool:
        if not self.state.active:
            return False # Can't guess when brb isn't running.

        if username in self.state.guesses.keys:
            return False # User already has a guess registered.

        self.state.guesses[username] = seconds

        return True # Guess added.

    #######################################################################
    def end(self) -> None:
        self.state.active = False
        self.state.stop_time = time.time()

        self.actual_secs = self.state.stop_time - self.state.start_time

    #######################################################################
    def winner(self) -> str:
        if self.state.active:
            return False # No winner when brb is still active.

        if len(self.state.guesses) == 0:
            return False # Nobody guessed, so there's no winner.

        return self._qualified.keys()[-1]

    #######################################################################
    def _qualified(self) -> Dict[str, int]:
        # Exclude any guess larger than the actual seconds.
        qualified = dict(filter(
            lambda secs: secs <= self.actual_secs,
            self.state.guesses.items(),
        ))

        # Sort the remaining guesses by number of seconds.
        qualified = dict(sorted(qualified.items(), key=lambda secs: secs))

        # The largest (last) guess is the one closest to actual_secs
        # without going over.
        return qualified


###########################################################################
# Twitch API Wrapper
###########################################################################

class TwitchApi:
    """
    Encapsulate http calls to Twitch's APIs.
    """

    def __init__(self, client_id, token):
        self.client_id = client_id
        self.token = token

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    #######################################################################
    def user(self, username):
        resp = self._get('helix/users', {'login': username})

        if not resp:
            OBS.debug("helix/users failed.")
            return False

        return resp # TODO: Refine this.

    #######################################################################
    def validate(self) -> Dict[str, any] | False:
        resp = self._get('oauth2/validate')

        if not resp:
            OBS.debug("oauth2/validate failed.")
            return False

        if not resp['login']:
            OBS.debug("OAuth token is not attached to a user.")
            return False

        if TwitchOAuth.OAUTH_SCOPES not in resp['scopes']:
            missing_scopes = ", ".join(set(TwitchOAuth.OAUTH_SCOPES) - set(resp['scopes']))
            OBS.debug(
                "OAuth token is lacking necessary scopes: (%s)" % (missing_scopes),
            )
            return False

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
        # logging.debug(url) # TODO: remove
        # logging.debug(extra_headers)# TODO: remove
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

            #     raise RuntimeError(f"Twitch: {detail}") from e

            # raise
            # logging.debug(e) # TODO: Refine

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
            'User-Agent': f"{SCRIPT_NAME} v{SCRIPT_VERSION}",
            'Accept': 'application/json',
            'Authorization': f"Bearer {self.token}",
            'Client-ID': self.client_id,
        } | extra


###########################################################################
# Twitch OAuth Wrapper
###########################################################################

class TwitchOAuth:
    """
    Coordinates the entire OAuth implicit grant process against Twitch.

    Starts up a background http server to receive Twitch's OAuth
    redirect payload, then opens a web browser window inside OBS to kick
    off the OAuth implicit authorization flow. On success, Twitch
    redirects the web browser back to the local http server.

    That server sends an HTML page with a Javascript payload that
    extracts the OAuth details from the URL fragement, and POSTs them to
    a different endpoint.

    That do_POST endpoint sends the oauth details back to us to write
    back the script's OBS internal storage. If THAT process completes
    successfully, the http server thread shuts down.
    """

    # BRB Timer for Chat
    # by beporter@users.sourceforge.net
    # https://dev.twitch.tv/console/apps/ja5swzyzsr1euwm0e53h1sxqhk553l
    APP_CLIENT_ID = "ja5swzyzsr1euwm0e53h1sxqhk553l"

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

    #######################################################################
    def __init__(self):
        self.server = None
        self.server_thread = None

        self.server_ready = threading.Event()

        # This is basically a "secret" we pass to Twitch during
        # OAuth flow, and Twitch passes it back. Ensures we're talking
        # to who we expect to talk to.
        self.oauth_state = None

        # The most recently obtained Twitch token information.
        self.oauth_result = {}

        # Lock protecting runtime state.
        self.state_lock = threading.Lock()

    #######################################################################
    def starter(self, pressed):
        """
        This is the callback target for the "Connect Twitch" button in
        script_properties().

        It starts up a single local http server in a background thread
        to listen for successful OAuth redirects.
        """
        if not pressed:
            return

        # Prevent multiple simultaneous OAuth flows.
        with self.state_lock:
            if (
                self.server_thread is not None
                and self.server_thread.is_alive()
            ):
                OBS.debug("OAuth flow already running.")
                return

            self.oauth_result = {}

            # Cryptographically random state value.
            self.oauth_state = secrets.token_urlsafe(32)

        # Start HTTP server.
        self.server_ready.clear()
        self.server_thread = threading.Thread(
            target=self.run_server,
            daemon=True,
            name=f"{SCRIPT_NAME} Twitch OAuth Receiver"
        )
        self.server_thread.start()

        # Wait until the server has successfully bound its port.
        if not self.server_ready.wait(timeout=2.0):
            OBS.debug("Timed out starting callback http server.")

            return

        if self.server is None:
            OBS.log("Callback server failed to start.")

            return

        # Build Twitch authorization URL.
        params = {
            "response_type": "token",
            "client_id": self.APP_CLIENT_ID,
            "redirect_uri": self.server.redirect_uri(),
            "scope": " ".join(self.OAUTH_SCOPES),
            "state": self.oauth_state,
        }

        authorization_url = (
            self.AUTHORIZE_URL
            + "?"
            + urllib.parse.urlencode(params)
        )

        OBS.debug("Opening Twitch authorization in browser...")

        webbrowser.open(authorization_url)

    #######################################################################
    def run_server(self):
        """
        The threaded http server manager. Spun off by starter().
        """
        try:
            self.server = TwitchOAuthServer()
            self.server_ready.set()
            OBS.info("HTTP server listening on: " + self.server.redirect_uri())
            self.server.serve_forever()

        except OSError as e:
            OBS.debug("Could not start HTTP server:" + e)
            self.server_ready.set()

        finally:
            if self.server is not None:
                try:
                    self.server.server_close()
                except Exception:
                    pass

            self.server = None

            OBS.info("HTTP server stopped.")

    #######################################################################
    def on_oauth_creds(self, data) -> bool:
        """
        Callback invoked by

        TwitchOAuthHandler.do_POST()
            -> script.on_oauth_creds()
                -> script.oauth.on_oauth_creds()

        when the browser sends the Twitch OAuth fragment back to us.

        This circuitous route is thanks to the inability to add
        __init__() arguments to TwitchOAuthHandler, which is instantiated
        separately for every http request.
        """
        state = data.get("state")
        with self.state_lock:
            # Verify the state parameter.
            if not self.oauth_state or state != self.oauth_state:
                OBS.debug("Returned OAuth state does't match stored state.")
                self.oauth_result = {
                    "error": "invalid_state"
                }

                return False

            # Twitch reports authorization failures in-payload:
            if "error" in data:
                OBS.debug(
                    "Authorization failed. %s: %s"
                    % (data.get("error"), data.get("error_description", "")),
                )
                self.oauth_result = {
                    "error": data.get("error", ""),
                    "error_description": data.get("error_description", ""),
                }

                return False

            access_token = data.get("access_token")
            if not access_token:
                OBS.debug("No access token received.")
                self.oauth_result = {
                    "error": "missing_access_token"
                }

                return False

            self.oauth_result = {
                "access_token": access_token,
                "token_type": data.get("token_type", "bearer"),
                "expiry": data.get("expiry", ""),
                "scope": data.get("scope", ""),
                "state": state,
            }

            OBS.info("Successfully received access token.")
            OBS.info("Scopes: %s" % data.get("scope", ""))
            # Don't print the actual access token to the OBS log.


###########################################################################
# HTTP Server
###########################################################################

class TwitchOAuthServer(http.server.ThreadingHTTPServer):
    """
    Minimal class for finding an available local tcp port and starting
    up an http server, using our custom request handler class.
    """

    allow_reuse_address = True

    # This host and these ports MUST be defined in the Twitch Developer
    # Console. If none of these are available on the machine running
    # OBS, the whole OAuth flow will fail.
    REDIRECT_HOST = "127.0.0.1"
    PORT_OPTIONS: list[int] = [8765, 4005, 99999]

    #######################################################################
    def __init__(self):
        super().__init__(
            (self.REDIRECT_HOST, self._find_port()),
            TwitchOAuthHandler
        )

    #######################################################################
    def _find_port(self) -> int:
        for port in self.PORT_OPTIONS:
            try:
                sock = socket.create_server(('', port), reuse_port = True)
                sock.close()
                return port

            except OSError as e:
                if e.errno == errno.EADDRINUSE:
                    continue

        raise RuntimeError(
            'No port available for local HTTP server from configured choices: %s'
            % " ".join(self.PORT_OPTIONS)
        ) # or ValueError?


###########################################################################
# HTTP Request Handler
###########################################################################

class TwitchOAuthHandler(http.server.BaseHTTPRequestHandler):
    """
    Handles HTTP requests made to the local web server.

    Instantiated per-request by TwitchOAuthServer.
    """

    NOT_FOUND_HTML: str = dedent(r"""
        <!DOCTYPE html>
        <html lang="en-US">
        <head>
            <meta charset="utf-8">
            <title>Twitch Authorization</title>
        </head>
        <body>
            <h1>404 Not Found</h1>
        </body>
        </html>
    """.strip())

    CALLBACK_HTML: str = dedent(r"""
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
                    // Twitch implicit grant puts the OAuth response
                    // in the URL fragment, not the query string.
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
                    const response = await fetch(COMPLETE_PATH, {
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
    """.strip())

    REDIRECT_PATH: str = "/oauth/callback"
    COMPLETE_PATH: str = "/oauth/complete"

    #######################################################################
    def redirect_uri(self) -> str:
        """
        This EXACT URI needs to be registered as the OAuth redirect URI
        for your Twitch application.
        """
        return f"http://{self.REDIRECT_HOST}:{self.server.server_port}{self.REDIRECT_PATH}"

    #######################################################################
    def do_GET(self):
        """
        Receive the successful Twitch OAuth redirect payload.

        The Twitch auth token is passed back to us in the URL _fragment_,
        so it is NOT available here. We return an HTML payload containing
        JavaScript that reads window.location.hash and POSTs it back to us.

        Ridiculous.
        """
        parsed = urllib.parse.urlparse(self.path)

        # Respond to everything (except our redirect destination) with a 404.
        if parsed.path != self.REDIRECT_PATH:
            self.respond(
                404, # TODO: HTTPStatus.NOT_FOUND
                "text/html; charset=utf-8",
                self.NOT_FOUND_HTML,
            )

            return

        # Send the browser our minimal HTML page with JS payload to
        # extract and POST the twitch OAuth creds.
        self.respond(
            200, # TODO: HTTPStatus.OK
            "text/html; charset=utf-8",
            self.CALLBACK_HTML
                .replace('COMPLETE_PATH', self.COMPLETE_PATH)
                .encode("utf-8"),
        )

    #######################################################################
    def do_POST(self):
        """
        Receives the OAuth information extracted from the URL fragment
        by do_GET().
        """
        parsed = urllib.parse.urlparse(self.path)

        # Respond to everything (except our POST destination) with a 404.
        if parsed.path != self.COMPLETE_PATH:
            self.json_error(404)

            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))

        except Exception as e:
            self.json_error(400, "Invalid POST data: %s" % e)

            return

        global script
        if not script.on_oauth_creds(data):
            self.json_error(500, "POST data rejected.")

            return

        self.respond(200, "application/json", b'{"ok":true}')

        # We invoke server.shutdown() from another thread so we don't
        # deadlock the HTTP request thread that's processing THIS call
        # to do_POST().
        if self.server is not None:
            threading.Thread(
                target=self.server.shutdown,
                daemon=True
            ).start()

    #######################################################################
    def json_error(self, code: int, detail: str = "") -> None:
        OBS.debug(f"HTTP Response: {code} {detail}")
        message = 'TODO: Get from httplib std lib using code'
        self.respond(
            code,
            "application/json",
            json.dumps({
                'error': True,
                'status': code,
                'message': message,
                'detail': detail,
            })
        )

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
        message = format % args
        OBS.debug(message)


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

    It provides methods for the OBS hook functions to call, in order to
    keep all business logic consolidated here.

    Classes used by BRBScript are expected not to use any method from
    the `obs` module unless that's their sole purview. This is mostly
    accomplished with dependency injection-- passing in handlers and
    callbacks to the classes.

    Architecture:

        BRBScript ---------------------------------------------|
            |          v        |        v       |             |
            |      TwitchApi    |   BRBSettings  |             |
            v                   v                v             v
        TwitchOauth       TwitchIRCClient   GuessManager  TimerRenderer
            |                   |                |             |
            v                   v                v             v
        TwitchOAuthServer  ChatMessage        BRBState   SourceGenerator
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
        * calls script.on_oauth_creds(data) -> writes oauth creds to script's OBS internal settings
        * web server thread shuts down.

    * script_tick() called for every rendered frame ->
        * (We do nothing.)

    * Streamer starts broadcasting ->
        * resets GuessManager
        * handler starts irc thread
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

    # def __init__(self):
    #     self.reset()

    #######################################################################
    def reset(self, settings):
        self.settings = BRBSettings()
        self.settings.from_data(settings)
        self.renderer = TimerRenderer(SOURCES.TEXT_TIMER, DEFAULTS.TIMER_TEXT)

        self.state = BRBState()
        self.guesses = GuessManager(self.state)

        # These two are initialized on an as-needed basis.
        self.irc: Optional[TwitchIRCClient] = None
        self.oauth: Optional[TwitchOAuth] = None

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

        self.reset(settings)
        OBS.event_register_handler()
        OBS.event_add(
            obs.OBS_FRONTEND_EVENT_FINISHED_LOADING,
            self.on_obs_ready,
        )
        OBS.event_add(
            obs.OBS_FRONTEND_EVENT_STREAMING_STARTING,
            self.on_streaming_starting,
        )
        OBS.event_add(
            obs.OBS_FRONTEND_EVENT_STREAMING_STOPPING,
            self.on_streaming_stopping,
        )

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

    #######################################################################
    def on_streaming_starting(self):
        """
        Script "main loop". Start up the IRC client to listen for chat
        commands.
        """
        # TODO: Rewrite this to use our own twitch oauth token instead of OBS's

        # self.settings.twitch_broadcaster_id = TODO
        # self.settings.twitch_username = TODO
        # self.settings.twitch_channel = TODO

        config = obs.obs_frontend_get_profile_config()
        token = obs.config_get_string(config, "Twitch", "Token")

        if token is None:
            OBS.info('OBS not logged into Twitch.')
            return

        api = TwitchApi(TwitchOAuth.APP_CLIENT_ID, token)
        channel = api.channel()
        username = api.username()
        callback = self.on_chat

        self.irc = TwitchIRCClient(channel, username, token, callback)
        # TODO: self.irc.start()

    #######################################################################
    def on_streaming_stopping(self):
        """
        Shutdown the background IRC client thread and reset script state.
        """
        # TODO: Stop all threads.
        # Save settings.
        # self.irc.stop()
        pass

    #######################################################################
    def on_oauth_creds(self, data) -> bool:
        """
        Callback invoked by

        TwitchOAuthHandler.do_POST()
            -> script.on_oauth_creds()

        when the browser sends the Twitch OAuth fragment back to us.
        """
        if self.oauth.on_oauth_creds(data):
            #self.settings.twitch_oauth_auth_token = self.oauth.oauth_result["TODO"]
            self.settings.twitch_oauth_expiry_at = self.oauth.oauth_result["expiry"]
            self.settings.twitch_oauth_access_token = self.oauth.oauth_result["access_token"]

            # TODO: Make TwitchApi calls to trade auth_token for access_token?

            return True

        return False

    #######################################################################
    # Chat Command Callbacks
    #######################################################################

    #######################################################################
    def on_chat(self, message: ChatMessage) -> None:
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
        Handler that's called when self.irc returns a ChatMessage with
        a !brb command.
        """
        raise NotImplementedError
        # TODO: Move this where it needs to go.
        # Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/README.md?plain=1#L472
        # eg.source_name = obs.obs_data_get_string(settings, "source")
        # obs.timer_remove(eg.update_text)
        # if eg.source_name != "":
        #     S.timer_add(eg.update_text, 1 * 1000)

        # renderer.set_text("00:00")
        # renderer.show()

        # renderer.set_text("04:31")

        # renderer.set_text(
        #     "04:31\nWinner: @Alice -0:15"
        # )

        # renderer.hide()

    #######################################################################
    def command_back(self, msg: ChatMessage):
        """
        Handler that's called when self.irc returns a ChatMessage with
        a !back command.
        """
        raise NotImplementedError

    #######################################################################
    def command_at(self, msg: ChatMessage):
        """
        Handler that's called when self.irc returns a ChatMessage with
        an !at command.
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

    Ref: TODO: Find the ref and clean this up.
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
            "Select the OBS Text Source from the active scene to use "
            f"to display the count-up timer when {COMMANDS.BRB} is active. "
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
    This lifecycle methods is called EARLY in the script's startup
    process. Before `script_properties()` is even called for the first
    time.

    The defaults defined here must track with the properties defined
    above in script_properties().

    If a given property is conditional or doesn't have a default, it
    still gets a comment here in the proper order to keep the two
    functions in lock step and so project-wide search turns up both
    places consistently.
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

    OBS.info(f"{SCRIPT_NAME} loaded.")

###########################################################################
def script_update(settings):
    """
    Called by OBS when the user has made modifications to the script's
    configuration in the GUI.

    Most relevant to us is if a "Connect Twitch" button was clicked and
    we have new oauth creds incoming soon.

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
    Called before OBS saves the script settings to the OBS user's local
    storage.
    """
    BRBSettings.to_data(settings)


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

    OBS.info(f"{SCRIPT_NAME} unloaded.")




############################################################
# TODO: Remove the below once it has been integrated.
############################################################

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
