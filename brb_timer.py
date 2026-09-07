"""
BRB Timer for OBS Studio
https://github.com/beporter/brb-timer
"""

from __future__ import annotations
import builtins
from collections import defaultdict
import contextlib
from dataclasses import dataclass
import datetime
import http
import http.client as http_client
import inspect
import json
import logging
import re
import select
import socket
import ssl
import sys
from textwrap import dedent
import threading
import time
from typing import Any, Callable, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request

#logging.basicConfig(level=logging.DEBUG)
# http_client.HTTPConnection.debuglevel = 1
# requests_log = logging.getLogger("requests.packages.urllib3")
# requests_log.setLevel(logging.DEBUG)
# requests_log.propagate = True

LOG_FILE = '/Users/beporter/Library/Application Support/obs-studio/brb-timer.log'
logging.basicConfig(filename=LOG_FILE,
                    filemode='a',
                    format='%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.DEBUG)

# logging.info("Running Urban Planning")
logger = logging.getLogger('brbtimer')
logger.propagate = True

try:
    import obspython as obs # type: ignore
except ImportError:
    # This bit of code replaces the real obspython module with a
    # testing mock. The script won't "work" without obs, but it'll
    # let you run `python -i brb_timer.py` to access an interactive
    # terminal without failing on
    # `NameError: name 'obs' is not defined.` to let you test the
    # rest of it.
    from unittest.mock import Mock
    obs = Mock()
    obs.script_log = lambda _lvl, msg: print(msg)

    logging.debug('obs module unavailable. Replaced with a mock.')


###########################################################################
# Types
###########################################################################

type ObsButtonCallback = Callable[..., bool]
type ObsEventCallback = Callable[..., bool]
type ObsRouterCallback = Callable [[int], None]
type ObsModifiedCallback = Callable[[], bool]
type OBSTimer = Callable[[], None]
type IrcMsgCallback = Callable[[ChatMessage], None]

###########################################################################
# Constants
###########################################################################

SCRIPT_NAME = "BRB Timer"
SCRIPT_VERSION = 1.0

# =========================================================================
# OBS Settings Defaults

class DEFAULTS:
    AUTO_HIDE_SECS = 120
    TIMER_TEXT = "--:--"

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
# Global Methods
###########################################################################

#--------------------------------------------------------------------------
def dispatch(callback: Callable) -> Callable:
    """
    The OBS SWIG scripting bridge can't call callback methods that belong
    to a class or instance object. It **must** be a module level bare
    function.

    So this method exists to give OBS a top-module-level method as a
    wrapper for an instance method.

    Example usage:

        class Events:
            def on_text_change(props, prop, settings):
                print('text change callback called')

        e = Events()

        def script_properties():
            # ...
            p = obs.obs_properties_add_text(props_obj, 'name', ...)

            obs.obs_property_set_modified_callback(
                p,
                dispatch(e.on_text_change)   # <-- wrap the real callback
            )
    """
    OBS.debug(f"Creating closure for: {callback.__qualname__}")

    def handle(*args, **kwargs):
        OBS.debug(f"Triggering callback: {callback.__qualname__}")
        result = callback(*args, **kwargs)
        OBS.debug(f"Callback {callback.__qualname__} returning: {result}")
        return result

    return handle


###########################################################################
# DateTime Convenience Class
###########################################################################

class DT:
    """
    Convenience datetime, timedelta and timezone helpers.

    Always uses UTC.
    """

    #----------------------------------------------------------------------
    @classmethod
    def epoch_secs_to_dt(self, epoch_secs: int) -> datetime.datetime:
        """
        Convert integer seconds since Jan 1 1970 to a DateTime object.
        """
        return datetime.datetime.fromtimestamp(
            int(epoch_secs),
            datetime.timezone.utc,
        )

    #----------------------------------------------------------------------
    @classmethod
    def secs_to_delta(self, secs: int) -> datetime.timedelta:
        """
        Convert integer seconds to a TimeDelta object.
        """
        return datetime.timedelta(seconds = secs)

    #----------------------------------------------------------------------
    @classmethod
    def duration_secs_to_dt(
        self,
        duration: int | datetime.timedelta,
    ) -> datetime.datetime:
        """
        Convert integer duration seconds to a DateTime object with UTC
        time elapsed since Jan 1 1970.

        Example:
            Current time: 2000-01-01T00:00:00Z
            duraction_secs = 500 # (8 mins, 20 secs)
            Returned time: 2000-01-01T00:08:20Z
        """
        if isinstance(duration, int):
            duration = datetime.timedelta(seconds = duration)

        return self.now() + duration

    #----------------------------------------------------------------------
    @classmethod
    def iso_str_to_dt(self, t_str: str) -> datetime.datetime:
        """
        Convert ISO string to a UTC DateTime object.

        Example: "2020-03-18T17:56:00Z"
        """
        return datetime.datetime \
            .fromisoformat(t_str) \
            .astimezone(datetime.timezone.utc)

    #----------------------------------------------------------------------
    @classmethod
    def now(self) -> datetime.datetime:
        """
        Get the current UTC time as a DateTime object.

        This is really annoying to have to type repeatedly. C'mon
        python, no 'now()' global?
        """
        return datetime.datetime.now(datetime.timezone.utc)

    #----------------------------------------------------------------------
    @classmethod
    def epoch(self) -> datetime.datetime:
        """
        Get a UTC DateTime object for Jan 1 1970.
        """
        return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    #----------------------------------------------------------------------
    @classmethod
    def timestamp(self) -> int:
        """
        Get the current number of integer seconds since Jan 1 1970.
        """
        return int(self.now().timestamp())

    #----------------------------------------------------------------------
    @classmethod
    def strfdelta(self, delta: datetime.timedelta) -> str:
        """
        Format the provided TimeDelta object as a `[[HH:]MM:]SS` string.
        """
        return (self.epoch() + delta).strftime(
            self.duration_fmt(delta.total_seconds()),
        )

    #----------------------------------------------------------------------
    @classmethod
    def strpdelta(self, time_str: str) -> datetime.timedelta | False:
        """
        Parse the provided `[[HH:]MM:]SS` string into a TimeDelta object.

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

        return datetime.timedelta(**time_params)

    #----------------------------------------------------------------------
    @classmethod
    def duration_str(
        self,
        elapsed: datetime.datetime | datetime.timedelta | int | float,
    ) -> str:
        """
        Format the provided DateTime/TimeDelta object's hours/mins/secs
        values, or int/float seconds, into a `[[HH:]MM:]SS` string.
        """
        match type(elapsed).__name__:
            case 'int' | 'float':
                secs = int(elapsed)
            case 'datetime': # Ignore year/month/day.
                if elapsed.tzname() is None:
                    elapsed = elapsed.astimezone(datetime.timezone.utc)
                secs = (elapsed - self.epoch()).total_seconds()
            case 'timedelta':
                secs = elapsed.total_seconds()
            case _:
                raise ValueError(f"Unrecognized duration format ({type(elapsed).__name__}) for value: {elapsed}")

        # Can't strftime a timedelta object. So just add the delta to
        # an epoch datetime object since we're only displaying
        # hours/mins/secs anyway.
        dt = self.epoch() + self.secs_to_delta(secs)
        fmt = self.duration_fmt(secs)

        return dt.strftime(fmt)

    #----------------------------------------------------------------------
    @classmethod
    def duration_fmt(self, secs: int) -> str:
        """
        Using the provided integer secs, return the shortest possible
        formatting string.

            DT.duration_fmt(45) -> '%S secs'
            DT.duration_fmt(90) -> '%M:%S'
            DT.duration_fmt(7200) -> '%H:%M:%S'
        """
        if secs >= 3600:
            fmt = '%H:%M:%S'
        elif secs >= 60:
            fmt = '%M:%S'
        else:
            fmt = '%S secs'

        return fmt

    #----------------------------------------------------------------------
    @classmethod
    def normalize_to_dt(
        self,
        input: Any,
    ) -> datetime.datetime:
        match type(input):
            case datetime.datetime:
                return input
            case builtins.str:
                return self.iso_str_to_dt(input)
            case builtins.int | builtins.float:
                return self.epoch_secs_to_dt(int(input))
            case _:
                OBS.warn(f"Fell through DT normalization: {input} ({type(input).__name__})")
                return self.epoch_secs_to_dt(0)


###########################################################################
# OBS Module Wrapper
###########################################################################

class OBS:
    """
    Minimal static wrapper around the `obs` module, to keep direct
    calls out of the rest of the script.

    Any method that returns something that needs to be released later
    should be used with `with OBS.thing() as thing:`
    """

    # ---------------------------------------------------------------------
    # Source
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    def source_exists(self, source_name: str) -> bool:
        """
        Returns True if a source with source_name is already present
        in the currently active scene.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/toggle_sceneitem_vis.py#L9-L13
        """
        with self.scene_current() as scene:
            # `scene` will get released by scene_current's context manager.
            if scene is None:
                return False

            # `sceneitem` does not need to be released.
            sceneitem = obs.obs_scene_find_source(scene, source_name)
            return (sceneitem is not None)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def source_by_name(self, name: str): # yield obs_source_t
        """
        Yield the source identified by the provided name, if possible.
        """
        try:
            source = obs.obs_get_source_by_name(name)
            if source is None:
                raise ValueError(
                    f"Source with name = '{name}' is not available."
                )

            yield source

        finally:
            if source is not None:
                obs.obs_source_release(source)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def source_name_in_scene(self, source_name: str, scene): # scene: obs_scene_t, yield obs_source_t
        """
        Yield the source from the provided scene, if present.

        If no scene is passed, we use the currently active scene.
        """
        if scene is None:
            raise ValueError('Scene not available.')

        try:
            # TODO: Might need to switch to the `_recursive` variant.
            source = obs.obs_scene_find_source(scene, source_name)
            if source is None:
                raise ValueError(
                    f"Source with name = '{source_name}' is not in current scene."
                )

            yield source

        # except Exception as e:
        #     if type(e) is RuntimeError and str(e) == "generator didn't yield":
        #         print('yield skipped, not an error.')
        #     else:
        #         raise e

        finally:
            if source is not None:
                obs.obs_source_release(source)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def source_create(
        self,
        source_type: str,
        source_name: str,
        settings = None, # obs_data_t
    ): # yield obs_source_t
        """
        Yield a brand new source.
        """
        with self.data_yield(settings) if settings is not None else self.data() as settings:
            try:
                source = obs.obs_source_create(
                    source_type,
                    source_name,
                    settings,
                    None,
                )
                if source is None:
                    raise ValueError(f"Unable to create a new Source object.")

                yield source

            finally:
                if source is not None:
                    obs.obs_source_release(source)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def source_create_text(
        self,
        source_name: str,
        text: str = "",
        source_type_id: str = None,
    ): # yield obs_source_t
        """
        Create and yield the first text source type available in the
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

                # Return the first successfully created text source.
                with self.source_create(source_id, source_name, settings) as source:
                    if source is not None:
                        OBS.info(
                            f"Using text source type '%s' (%s)."
                                % (display_name, source_id),
                            )

                        yield source

                        return

            raise ValueError(f"Couldn't find an available text source type.")

    #----------------------------------------------------------------------
    @classmethod
    def source_update(
        self,
        source, # obs_source_t
        changes: Dict[str, any],
    ) -> None:
        """
        Apply settings changes to the provided source.
        """
        with self.data_set_all(None, changes) as new_settings:
            obs.obs_source_update(source, new_settings)

    #----------------------------------------------------------------------
    @classmethod
    def source_save(self, source) -> bool: # source: obs_source_t
        """
        Save the provided source to OBS's persistent storage.
        """
        return obs.obs_save_source(source)

    #----------------------------------------------------------------------
    # @classmethod
    # def source_uuid(self, source) -> str: # source: obs_source_t
    #     """
    #     Get the OBS internal UUID of the provided source.
    #     """
    #     return obs.obs_source_get_uuid(source)


    # ---------------------------------------------------------------------
    # Scene
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def scene_current(self): # yield obs_scene_t
        """
        Yields an obs_scene object and auto-releases afterward.

        Usage example:

            with OBS.scene_current() as scene:
                # do something with `scene`.
        """
        try:
            # Must be released with obs_source_release()
            scene_source = obs.obs_frontend_get_current_scene()

            # Does not need to be released!
            scene = obs.obs_scene_from_source(scene_source)

            yield scene

        finally:
            if scene_source is not None:
                obs.obs_source_release(scene_source)


    # ---------------------------------------------------------------------
    # Scene Item
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def scene_add(
        self,
        scene, # obs_scene_t
        source, # obs_source_t
    ): # yield obs_sceneitem_t
        """
        Yields a sceneitem object resulting from adding the provided
        source to the provided scene.

        Uses the currently active scene if none is provided.
        """
        try:
            sceneitem = obs.obs_scene_add(scene, source)
            if sceneitem is None:
                raise ValueError(
                    "Unable to create a sceneitem for source."
                )

            yield sceneitem

        finally:
            if sceneitem is not None:
                obs.obs_sceneitem_release(sceneitem)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def sceneitem_by_name(
        self,
        name: str,
        scene, # obs_scene_t
    ): # yield obs_sceneitem_t
        """
        Yields a sceneitem object matching the provided name.
        """
        try:
            # TODO: Might need to use the recursive version of this method.
            sceneitem = obs.obs_scene_find_source(scene, name)
            # if sceneitem is None:
            #     raise ValueError(
            #         f"A sceneitem with name = '{name}' could not be found."
            #     )

            yield sceneitem

        finally:
            if sceneitem is not None:
                obs.obs_sceneitem_release(sceneitem)

    #----------------------------------------------------------------------
    @classmethod
    def sceneitem_position_set(
        self,
        sceneitem, # obs_sceneitem_t
        alignment: int, # OBS_ALIGN_*
        pos_x: int = None,
        pos_y: int = 0,
        z_index: int = obs.OBS_ORDER_MOVE_TOP, # OBS_ORDER_*
    ) -> None:
        # Set on-screen alignment.
        obs.obs_sceneitem_set_alignment(sceneitem, alignment)

        # Set on-screen position.
        if pos_x is None:
            vi = obs.obs_video_info()
            obs.obs_get_video_info(vi)
            pos_x = vi.base_width

        obs.obs_sceneitem_set_pos(sceneitem, OBS.vec2(pos_x, pos_y))

        # Set z-index to top.
        obs.obs_sceneitem_set_order(sceneitem, z_index)

        # Set item bounds.
        # obs.obs_sceneitem_set_bounds_type(sceneitem, obs.OBS_BOUNDS_SCALE_TO_WIDTH)
        # obs.obs_sceneitem_set_bounds_alignment(sceneitem, obs.OBS_ALIGN_CENTER)
        # obs.obs_sceneitem_set_bounds(sceneitem, obs.vec2(400, 100))

    #----------------------------------------------------------------------
    @classmethod
    def sceneitem_add_transition(
        self,
        sceneitem, # obs_sceneitem_t
        transition_source, # obs_source_t
        visibility: str,
        duration: int = 300,
    ) -> None:
        # Input validation.
        if visibility not in ("show", "hide"):
            raise ValueError("visibility must be 'show' or 'hide'")

        # Attach the transition to the scene item.
        obs.obs_sceneitem_set_transition(
            sceneitem,
            visibility == "show",
            transition_source,
        )

        # Set transition duration.
        obs.obs_sceneitem_set_transition_duration(
            sceneitem,
            visibility == "show",
            duration,
        )

    #----------------------------------------------------------------------
    @classmethod
    def sceneitem_set_visible(
        self,
        sceneitem, # obs_sceneitem_t
        show: bool,
    ):
        """
        Tells OBS to show or hide the provided sceneitem on-screen.

        Ref: https://github.com/BraatheSaaS/OBS-Studio-Python-Scripting-Cheatsheet-obspython-Examples-of-API#toggle-sceneitem-visibility
        """
        return obs.obs_sceneitem_set_visible(sceneitem, show)

    #----------------------------------------------------------------------
    @classmethod
    def sceneitem_visible(
        self,
        sceneitem, # obs_sceneitem_t
    ) -> bool:
        """
        Returnes true if the provided sceneitem is currently visible
        on-screen.
        """
        return obs.obs_sceneitem_visible(sceneitem)


    # ---------------------------------------------------------------------
    # Transition
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def transition_source_create(
        self,
        source_name: str,
        transition_type: str,
        visibility: str,
        direction: str,
    ):
        """
        Creates a new transition Source object with the provided settings.

        Returns False if any part of the process fails.
        """
        # Input validation.
        if visibility not in ("show", "hide"):
            raise ValueError("visibility must be 'show' or 'hide'")

        if direction not in ("left", "right"):
            raise ValueError("direction must be 'left' or 'right'")

        try:
            with self.data() as settings:
                obs.obs_data_set_string(settings, "direction", direction)
                transition = obs.obs_source_create(
                    transition_type,
                    source_name,
                    settings,
                    None,
                )

                if transition is None:
                    raise ValueError(
                        f"Failed to create {visibility} {transition_type} source."
                    )

                yield transition

        finally:
            if transition is not None:
                obs.obs_source_release(transition)

    #----------------------------------------------------------------------
    @classmethod
    def transition_duration(
        self,
        transition_sceneitem, # obs_sceneitem_t
        visibility: str,
    ) -> int | None:
        try:
            duration = obs.obs_sceneitem_get_transition_duration(
                transition_sceneitem,
                visibility == "show",
            )

        except:
            duration = None

        return duration


    # ---------------------------------------------------------------------
    # Data
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    def vec2(self, x: int, y: int): # -> obs_vec2_t
        v = obs.vec2()
        v.x = x
        v.y = y
        return v

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def text_settings(
        self,
        text: str,
        width: int = None,
        wrap: bool = False,
        outline: bool = True,
        drop_shadow: bool = False,
        color_top: int = 0xffffffff, # Format: 0xrrggbbaa
        color_bottom: int = 0xffffffff,
        font_face: str = "Monaco",
        font_style: str = "Regular",
        font_size_px: int = 96,
    ): # yield obs_data_t
        """
        Helper that yields an obs_data object pre-configured with
        our stock text Source settings.
        """
        with self.data() as settings:
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

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def data(
        self,
        source_settings = None, # obs_data_t
    ): # yield obs_data_t
        """
        Helper that yields an obs_data object either from the
        passed settings, or from scratch. Auto-releases after use.

        Usage example:

        with OBS.data() as d:
            # do something with `d`.
        """
        try:
            if source_settings:
                data = obs.obs_source_get_settings(source_settings)
            else:
                data = obs.obs_data_create()

            if data is None:
                raise ValueError(
                    f"Could not create a new data object."
                )

            yield data

        finally:
            if data is not None:
                obs.obs_data_release(data)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def data_yield(
        self,
        data, # obs_data_t
    ): # yield obs_data_t
        """
        Yield the passed data object. Does NOT release it.
        """
        try:
            yield data
        finally:
            pass

    #----------------------------------------------------------------------
    @classmethod
    def data_get(
        self,
        data, # obs_data_t
        key: str,
        fallback = None,
    ) -> any:
        all = self.data_get_all(data)
        if key in all.keys():
            return all[key]
        else:
            return fallback

    #----------------------------------------------------------------------
    @classmethod
    def data_get_all(
        self,
        data, # obs_data_t
    ) -> object:
        """
        Return the provided data_t object as a generic python object.
        """
        return json.loads(self.data_get_json(data))

    #----------------------------------------------------------------------
    @classmethod
    def data_get_json(
        self,
        data, # obs_data_t
    ) -> str:
        """
        Return the provided data_t object as a json string.
        """
        return obs.obs_data_get_json(data)

    #----------------------------------------------------------------------
    @classmethod
    @contextlib.contextmanager
    def data_set_all(
        self,
        data, # obs_data_t
        changes: dict[str, any],
    ): # yield obs_data_t
        """
        Currently only handles scalar values.
        """
        with data if data is not None else self.data() as new_settings:
            for (k, v) in changes.items():
                self.data_set(new_settings, k, v)

            yield new_settings

    #----------------------------------------------------------------------
    @classmethod
    def data_set(
        self,
        data, # obs_data_t
        key: str,
        val: any,
        **args,
    ) -> None:
        """
        Attempts to call the appropriate obs_data_set_*()` method by
        examining the python type of the supplied value. None values
        are not written to the data object at all to ensure they are
        not deserialized back to us weirdly.
        """
        match type(val).__name__:
            case 'bool':
                obs.obs_data_set_bool(data, key, val)
            case 'float':
                obs.obs_data_set_double(data, key, val)
            case 'int':
                obs.obs_data_set_int(data, key, val, **args)
            case 'str':
                obs.obs_data_set_string(data, key, val)
            case 'NoneType':
                return
            case _:
                self.warn(
                    f"Fell through type detection. {key} = {val} ({type(val).__name__}). "
                    "Casting to string."
                )
                obs.obs_data_set_string(data, key, str(val))

    #----------------------------------------------------------------------
    @classmethod
    def data_set_default(
        self,
        data, # obs_data_t
        key: str,
        val: any,
        **args,
    ) -> None:
        match type(val).__name__:
            case 'bool':
                obs.obs_data_set_default_bool(data, key, val)
            case 'float':
                obs.obs_data_set_default_double(data, key, val)
            case 'int':
                obs.obs_data_set_default_int(data, key, val, **args)
            case 'str':
                obs.obs_data_set_default_string(data, key, val)
            case 'NoneType':
                return
            case _:
                self.warn(
                    f"Fell through type detection. {key} = {val} ({type(val).__name__}). "
                    "Casting default to string."
                )
                obs.obs_data_set_default_string(data, key, str(val))


    # ---------------------------------------------------------------------
    # Events & Timers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    def event_register_router(self, router: ObsRouterCallback) -> None:
        """
        This method is the public interface for registering our wrapper's
        event router method (below).
        """
        obs.obs_frontend_add_event_callback(lambda ev: router(ev))

    #----------------------------------------------------------------------
    @classmethod
    def event_unregister_router(self, router: ObsRouterCallback) -> None:
        if router is not None:
            obs.obs_frontend_remove_event_callback(router)

    #----------------------------------------------------------------------
    @classmethod
    def event_name(self, obs_const: int) -> str:
        """
        Converts an OBS constant to its string representation. Used for
        logging.

            OBS.event_name(obs.EVENT_NAME_HERE) -> 'EVENT_NAME_HERE'
        """
        names = {v: n for n, v in vars(obs).items()
            if n.startswith('OBS_FRONTEND_EVENT_')}
        return names[obs_const] if obs_const in names.keys() else 'UNRECOGNIZED_EVENT'

    #----------------------------------------------------------------------
    @classmethod
    def event_remove_self(self):
        """
        Call this from _inside_ a callback to remove the callback as a
        handler.
        """
        obs.remove_current_callback()

    #----------------------------------------------------------------------
    @classmethod
    def timer_name(self, timer_func: OBSTimer) -> str:
        TIMER_FUNC_PREFIX: str = '__obs_timer_proxy__'
        return f"{TIMER_FUNC_PREFIX}{timer_func.__name__}"

    #----------------------------------------------------------------------
    @classmethod
    def timer_running(self, timer_func: OBSTimer) -> bool:
        """
        This is a bit crude since the actual timer method gets called
        repeatedly, but the existence of the module level proxy function
        is 'good enough' for our needs.
        """
        return self.timer_name(timer_func) in globals().keys()

    #----------------------------------------------------------------------
    @classmethod
    def timer_add(self, timer: OBSTimer, millisecs: int) -> OBSTimer:
        """
        Creates a new global function named like the provided timer
        function and adds that global function to OBS as a timer.

        OBS docs specifically warns againt using a python instance
        method as a timer.

        Usage:
            OBS.timer_add(instance.my_func, millisecs)

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/README.md?plain=1#L472
        """
        func_name = self.timer_name(timer)
        if func_name in globals().keys():
            raise ValueError(
                "Can't shadow the provided timer. Global function "
                f"already exists: {func_name}"
            )

        main = sys.modules[__name__]
        setattr(main, func_name, dispatch(timer))
        shadow = getattr(main, func_name)
        obs.timer_add(shadow, millisecs)
        return shadow

    #----------------------------------------------------------------------
    @classmethod
    def timer_remove(self, timer: OBSTimer, throw: bool = False) -> bool:
        """
        Remove the provided "clean" timer function from OBS timers.

        Under the hood, this also removes the global function that
        proxies the calls to satisfy OBS's / SWIG's requirement that
        timers be top-level module functions.
        """
        func_name = self.timer_name(timer)
        if func_name not in globals().keys():
            msg = str(
                f"Can't remove the shadow for the provided timer: {timer} "
                f"Global function doesn't exist: {func_name}"
            )
            if throw:
                raise ValueError(msg)
            else:
                self.debug(msg)
                return False

        main = sys.modules[__name__]
        shadow = getattr(main, func_name)
        obs.timer_remove(shadow)
        delattr(main, func_name)
        return True

    # ---------------------------------------------------------------------
    # GUI
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    def properties_get(self, props, name: str): # -> obs_property_t
        return obs.obs_properties_get(props, name)

    #----------------------------------------------------------------------
    @classmethod
    def properties_create(self): # -> obs_properties_t
        return obs.obs_properties_create()

    #----------------------------------------------------------------------
    @classmethod
    def frontend_open_source_props(self, name: str) -> None:
        with self.source_by_name(name) as source:
            obs.obs_frontend_open_source_properties(source)

    #----------------------------------------------------------------------
    @classmethod
    def properties_visibility_set(
        self,
        props,
        prop_names: dict[str, bool],
    ) -> bool:
        for name, show in prop_names.items():
            if show:
                self.property_show(props, name)
            else:
                self.property_hide(props, name)

    #----------------------------------------------------------------------
    @classmethod
    def property_show(self, props, name: str) -> None:
        OBS.debug(f"Checking for property to show: {name}.")
        p = obs.obs_properties_get(props, name)
        if p is not None:
            OBS.debug(f"Making property {name} visible.")
            obs.obs_property_set_visible(p, True)
            OBS.debug(f"Property {obs.obs_property_name(p)} visibility is: {'visible' if obs.obs_property_visible(p) else 'hidden'}.")

    #----------------------------------------------------------------------
    @classmethod
    def property_hide(self, props, name: str) -> None:
        OBS.debug(f"Checking for property to hide: {name}.")
        p = obs.obs_properties_get(props, name)
        if p is not None:
            OBS.debug(f"Making property {name} hidden.")
            obs.obs_property_set_visible(p, False)
            OBS.debug(f"Property {obs.obs_property_name(p)} visibility is: {'visible' if obs.obs_property_visible(p) else 'hidden'}.")


    # ---------------------------------------------------------------------
    # Logging
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    @classmethod
    def error(self, msg: str) -> None:
        self._log(msg, obs.LOG_ERROR)

    #----------------------------------------------------------------------
    @classmethod
    def warn(self, msg: str) -> None:
        self._log(msg, obs.LOG_WARNING)

    #----------------------------------------------------------------------
    @classmethod
    def info(self, msg: str) -> None:
        self._log(msg, obs.LOG_INFO)

    #----------------------------------------------------------------------
    @classmethod
    def debug(self, msg: str) -> None:
        self._log(msg, obs.LOG_DEBUG)

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

        obs.script_log(level, f"[{calling_method}] {msg}")


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

    #----------------------------------------------------------------------
    def __init__(
        self,
        obs_module, # python module
        props_container, # obs_properties_t
    ):
        self.obs = obs_module
        self.props = props_container

    #----------------------------------------------------------------------
    def text(
        self,
        name: str,
        label: str = None,
        type: int = obs.OBS_TEXT_DEFAULT, # OBS_TEXT_*
        modified_callback: Optional[ObsModifiedCallback] = None,
    ) -> None:
        """
        Ref: https://docs.obsproject.com/reference-properties#c.obs_property_set_modified_callback
        """
        p = self.obs.obs_properties_add_text(
            self.props,
            name,
            label if label is not None else name.replace("_", " ").capitalize(),
            type,
        )
        if modified_callback is not None:
            #OBS.debug(f"modified_callback is: {modified_callback!r}")
            self.obs.obs_property_set_modified_callback(p,
                dispatch(modified_callback),
            )

    #----------------------------------------------------------------------
    def twitch_button(
        self,
        keyword: str,
        url: str = None,
        text_type: int = obs.OBS_TEXT_INFO_NORMAL, # OBS_TEXT_INFO_*
        long_desc: str = '',
        show = True,
    ) -> None:
        if url is not None:
            b = self.url_button(
                f"twitch_{keyword}",
                f"{keyword.capitalize()} Twitch",
                url,
                text_type,
                long_desc,
            )
        else:
            b = self.button(
                f"twitch_{keyword}",
                f"{keyword.capitalize()} Twitch",
                getattr(script, f"on_twitch_{keyword}"),
                text_type,
                long_desc,
            )

        if not show:
            self.obs.obs_property_set_visible(b, show)

        return b

    #----------------------------------------------------------------------
    def url_button(
        self,
        base_name: str,
        label: str,
        url: str,
        text_type: int = obs.OBS_TEXT_INFO_NORMAL, # OBS_TEXT_INFO_*
        long_desc: str = '',
        wrap: bool = True,
    ): # -> obs_property_t
        b = self.button(base_name, label, None, text_type, long_desc, wrap)
        self.obs.obs_property_button_set_type(b, self.obs.OBS_BUTTON_URL)
        self.obs.obs_property_button_set_url(b, url)
        OBS.debug(f"Creating url button for {base_name} to: {url}")

        return b # In case any further modification is desired.

    #----------------------------------------------------------------------
    def button(
        self,
        base_name: str,
        label: str,
        callback: ObsButtonCallback = None,
        text_type: int = obs.OBS_TEXT_INFO_NORMAL, # OBS_TEXT_INFO_*
        long_desc: str = '',
        wrap: bool = True,
    ): # obs_property_t
        """
        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/example_class.py#L41
        """
        if callback is None:
            callback = lambda *args: None

        b = self.obs.obs_properties_add_button(
            self.props,
            f"{base_name}_button",
            label,
            dispatch(callback),
        )
        # Ref: https://docs.obsproject.com/reference-properties#c.obs_property_set_long_description
        if len(long_desc) > 0:
            # i = self.obs.obs_properties_add_text(
            #     self.props,
            #     f"{base_name}_info",
            #     long_desc,
            #     self.obs.OBS_TEXT_INFO,
            # )
            # self.obs.obs_property_text_set_info_type(i, text_type)
            # self.obs.obs_property_text_set_info_word_wrap(i, wrap)
            self.obs.obs_property_set_long_description(b, long_desc)

        return b # In case any further modification is desired.

    #----------------------------------------------------------------------
    # def text_source_list(self, name: str, desc: str, long_desc: str = '') -> None:
    #     # Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5/src/duplicate_source.py#L51-L63
    #     sources = self.obs.obs_enum_sources()
    #     if sources is not None:
    #         p = self.obs.obs_properties_add_list(
    #             self.props,
    #             name,
    #             desc,
    #             self.obs.OBS_COMBO_TYPE_LIST,
    #             self.obs.OBS_COMBO_FORMAT_STRING,
    #         )
    #         self.obs.obs_property_set_modified_callback(p, lambda props, prop, settings: script.on_props_should_update(props, prop, settings))
    #         self.obs.obs_property_list_add_string(p, "", "")
    #         self.obs.obs_property_list_add_string(p, "(Add New)", "__add_new__")
    #         for source in sources:
    #             source_type_id = self.obs.obs_source_get_unversioned_id(source)
    #             source_name = self.obs.obs_source_get_name(source)
    #             source_uuid = self.obs.obs_source_get_uuid(source)
    #             # There doesn't seem to be another way to identify these.
    #             icon_type = self.obs.obs_source_get_icon_type(source_type_id)
    #             if icon_type == self.obs.OBS_ICON_TYPE_TEXT:
    #                 self.obs.obs_property_list_add_string(p, source_name, source_uuid)

    #         i = self.obs.obs_properties_add_text(
    #             self.props,
    #             f"{name}_info",
    #             long_desc,
    #             self.obs.OBS_TEXT_INFO,
    #         )
    #         self.obs.obs_property_text_set_info_type(i, self.obs.OBS_TEXT_INFO_NORMAL)
    #         self.obs.obs_property_text_set_info_word_wrap(i, True)

    #         self.obs.source_list_release(sources)

    #----------------------------------------------------------------------
    def int_with_unit(
        self,
        name: str,
        desc: str,
        min: int,
        max: int,
        step: int,
        unit: str = None,
        modified_callback: Optional[ObsModifiedCallback] = None,
    ) -> None:
        a = self.obs.obs_properties_add_int(self.props, name, desc, min, max, step)
        if unit is not None:
            self.obs.obs_property_int_set_suffix(a, unit)
        if modified_callback is not None:
            self.obs.obs_property_set_modified_callback(a, dispatch(modified_callback))


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

    #----------------------------------------------------------------------
    @classmethod
    def create_source(
        self,
        OBS: OBS, # BRBTimer.OBS class (not an instance)
        source_name: str,
        text: str = "hello world",
        text_source_id: str = None, # Differs by platform. See OBS.source_create_text
    ) -> bool:
        """
        If we were provided a source_name that doesn't exist in the
        active scene, create one and add it into the active scene using
        some defaults that the OBS user can subsequently tweak.

        Ref: https://github.com/upgradeQ/Streaming-Software-Scripting-Reference/blob/b876ee8e5a57/src/add_nested.py#L16
        Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/plugins/text-freetype2/text-freetype2.c#L170
        """
        if OBS.source_exists(source_name):
            OBS.info(
                f"Source '{source_name}' already exists in current scene."
                "Nothing created."
            )
            return False

        # Create the text source for the timer display
        with OBS.source_create_text(
            source_name,
            text,
            text_source_id,
        ) as source:
            OBS.source_save(source)

            # Add source to scene.
            # Ref: https://github.com/obsproject/obs-studio/blob/32.0.4/libobs/obs.h#L153
            with OBS.scene_current() as scene:
                if scene is None:
                    OBS.error("Current frontend source is not an OBS scene.")
                    return False

                # Add transitions to scene item.
                with OBS.scene_add(scene, source) as sceneitem:
                    if sceneitem is None:
                        OBS.error(
                            f"Could not add source '{source_name}' to current scene.",
                        )
                        return False

                    OBS.sceneitem_position_set(
                        sceneitem,
                        # These constants don't work for some reason.
                        #obs.OBS_ALIGN_RIGHT | obs.OBS_ALIGN_TOP,
                        ((1 << 1) | (1 << 2))
                    )

                    with OBS.transition_source_create(
                        SOURCES.TRANSITION_SHOW,
                        self.SHOW_TRANSITION_TYPE,
                        'show',
                        self.SHOW_TRANSITION_DIR,
                    ) as show_trans:
                        OBS.source_save(show_trans)
                        OBS.sceneitem_add_transition(
                            sceneitem,
                            show_trans,
                            'show',
                            self.TRANSITION_DURATION_MS,
                        )

                    with OBS.transition_source_create(
                        SOURCES.TRANSITION_HIDE,
                        self.HIDE_TRANSITION_TYPE,
                        'hide',
                        self.HIDE_TRANSITION_DIR,
                    ) as hide_trans:
                        OBS.source_save(hide_trans)
                        OBS.sceneitem_add_transition(
                            sceneitem,
                            hide_trans,
                            'hide',
                            self.TRANSITION_DURATION_MS,
                        )

        return True


###########################################################################
# Twitch API Wrapper
###########################################################################

class TwitchApi:
    """
    Encapsulate http calls to Twitch's APIs.
    """

    #----------------------------------------------------------------------
    def __init__(self, client_id: str, token: str):
        self.client_id = client_id
        self.token = token

    #----------------------------------------------------------------------
    def validate(self) -> Dict[str, any] | False:
        """
        Pass an oauth token to determine if it's still valid, and who
        it belongs to.

        Ref: https://dev.twitch.tv/docs/authentication/validate-tokens#how-to-validate-a-token
        """
        resp = self._get('oauth2/validate')

        if not resp:
            OBS.error("oauth2/validate failed.")
            return False

        if not resp['login']:
            OBS.error("OAuth token is not attached to a user.")
            return False

        if not set(TwitchOAuth.OAUTH_SCOPES).issubset(resp['scopes']):
            missing_scopes = ", ".join(set(TwitchOAuth.OAUTH_SCOPES) - set(resp['scopes']))
            OBS.error(
                "OAuth token is lacking necessary scopes: (%s)" % (missing_scopes),
            )
            return False

        return {
            'client_id': resp.get('client_id', ''),
            'login': resp.get('login', ''),
            'scopes': resp.get('scopes', []),
            'broadcaster_id': int(resp.get('user_id', '')),
            'expires_in': resp.get('expires_in', None), # int seconds
            'expires_at': DT.duration_secs_to_dt(resp.get('expires_in', None)), # datetime
        }

    #----------------------------------------------------------------------
    def channel(self, broadcaster_id: int) -> Dict[str, any]:
        """
        Get the provided broadcaster's channel details.

        Ref: https://dev.twitch.tv/docs/api/reference#get-channel-information
        """
        resp = self._get('helix/channels', {'broadcaster_id': broadcaster_id})

        if not resp:
            OBS.error(
                "helix/channels failed for "
                f"broadcaster_id: {broadcaster_id}"
            )
            return False

        if not resp.get('data') or not resp.get('data', [False])[0]:
            OBS.error(
                "No channel data returned for "
                f"broadcaster_id: {broadcaster_id}"
            )
            return False

        channel = resp.get('data')[0]

        return {
            'broadcaster_id': int(channel.get('broadcaster_id', -1)),
            'broadcaster_login': channel.get('broadcaster_login', ''),
            'broadcaster_name': channel.get('broadcaster_name', ''),
            'broadcaster_language': channel.get('broadcaster_language', 'en'),
            'game_id': int(channel.get('game_id', -1)),
            'game_name': channel.get('game_name', ''),
            'title': channel.get('title', ''),
            'tags': channel.get('tags', []),
            'content_classification_labels': channel.get('content_classification_labels', []),
            'is_branded_content': bool(channel.get('is_branded_content', False)),
        }

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def _get(
            self,
            path: str,
            params: dict[str, str|int] = {},
            extra_headers: dict[str, str|int] = {},
        ) -> object | False:
        server = self._server(path)
        url = f"{server}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(extra_headers))

        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            match e.code:
                case 401:
                    try:
                        detail = json.load(e.fp)
                    except Exception:
                        detail = "unauthorized"

                # Ref: https://dev.twitch.tv/docs/api/guide/#twitch-rate-limits
                case http.HTTPStatus.TOO_MANY_REQUESTS:
                    total = e.headers.get('Ratelimit-Limit')
                    remaining = e.headers.get('Ratelimit-Remaining')
                    reset = DT.epoch_secs_to_dt(e.headers.get('Ratelimit-Reset'))
                    detail = (
                        f"Request for {e.url} was rate limited. "
                        f"({remaining}/{total} requests remaining. "
                        f"Reset at {reset!s}.)"
                    )

                case _:
                    detail = e.reason

            OBS.error(detail)

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
            'Client-ID': self.client_id,
        } | extra


###########################################################################
# Twitch OAuth Wrapper
###########################################################################

class TwitchOAuth:
    """
    Coordinates the entire OAuth implicit grant process against Twitch.

    - Generates the starting URL to be opened in the user's browser.
    - That URL sets up the necessary OAuth query args and the user
      submits to id.twitch.tv.
    - Twitch redirects back to the GH Pages hosted landing page, where
      some JS prints out the access token.
    - The user copies and pastes the access token into the OBS
      properties for this script.
    - The script fires Twitch API calls to validate the token, get
      expiration information, and fetch broadcaster and channel info
      necessary for IRC bot use.
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
    def __init__(self, settings: BRBSettings):
        self.settings: BRBSettings = settings
        self.api: Optional[TwitchApi] = None # Initialized on use.

    #----------------------------------------------------------------------
    @classmethod
    def kickoff_url(self) -> str:
        """
        Points to this project's Github Pages "start" page.

        That page handles all of the OAuth scope itself directly, so
        no params are strictly required here.
        """
        # TODO: Toggle on debug: `./local_http.sh` --> "http://localhost:8765/start.html"
        return self.KICKOFF_URL

    #----------------------------------------------------------------------
    def creds_present(self) -> bool:
        if (
            self.settings.twitch_oauth_access_token is None
            or len(self.settings.twitch_oauth_access_token) == 0
            or self.settings.twitch_oauth_expiry_at is None
            or self.settings.twitch_oauth_expiry_at == 0
        ):
            OBS.info('No Twitch OAuth token saved.')
            return False

        if self.creds_expired():
            return False

        return True

    #----------------------------------------------------------------------
    def creds_expired(self) -> bool:
        expiry = DT.normalize_to_dt(
            self.settings.twitch_oauth_expiry_at
        )

        self.settings.twitch_oauth_expiry_at = int(
            expiry.timestamp(),
        )

        if (
            self.settings.twitch_oauth_access_token is not None
            and self.settings.twitch_oauth_access_token != ''
            and self.settings.twitch_oauth_expiry_at is not None
            and self.settings.twitch_oauth_expiry_at != 0
            and DT.now() > expiry
        ):
            OBS.info('Stored Twitch OAuth token has expired.')
            return True

        return False

    #----------------------------------------------------------------------
    def creds_set(self, access_token: str, expires_at: int = 0) -> bool:
        self.clear_settings() # Have to invalidate existing api instance when token changes.

        # Cast expires at from string,
        if isinstance(self.settings.twitch_oauth_expiry_at, str):
            expires_at = DT.iso_str_to_dt(self.settings.twitch_oauth_expiry_at)

        # or datetime oject, to int.
        if isinstance(expires_at, datetime.datetime):
            expires_at = int(expires_at.timestamp())

        self.settings.twitch_oauth_access_token = access_token.strip()
        self.settings.twitch_oauth_expiry_at = expires_at

        if not self.user_get():
            OBS.error("New Twitch access token is not valid. Removing existing settings.")
            self.clear_settings()

            return False

        OBS.info("New Twitch access token is valid. Repopulating user and channel info.")
        self.channel_get()

        return True

    #----------------------------------------------------------------------
    def user_present(self) -> bool:
        return (
            self.settings.twitch_broadcaster_id > 0
            and len(self.settings.twitch_username) > 0
        )

    #----------------------------------------------------------------------
    def user_get(self) -> Dict[str, any] | False:
        if not self.user_present():
            if self.creds_present():
                validated = self._api().validate()
                if not validated:
                    OBS.info('Twitch OAuth token validation failed.')

                    return False

                OBS.info('Twitch OAuth token validation succeeded.')
                self.settings.twitch_oauth_expiry_at = int(validated['expires_at'].timestamp())
                self.user_set(validated['broadcaster_id'], validated['login'])

        return {
            'id': self.settings.twitch_broadcaster_id,
            'name': self.settings.twitch_username,
        }

    #----------------------------------------------------------------------
    def user_set(self, broadcaster_id: int, username: str) -> bool:
        self.settings.twitch_broadcaster_id = broadcaster_id
        self.settings.twitch_username = username

        return True

    #----------------------------------------------------------------------
    def channel_present(self) -> bool:
        return (
            self.settings.twitch_broadcaster_id > 0
            and len(self.settings.twitch_channel) > 0
        )

    #----------------------------------------------------------------------
    def channel_get(self) -> str:
        """
        Fetches and stores the OBS user's Twitch channel name on demand.

        This is initially called really early for the script's OBS GUI
        properties, so we do a bit more guarding/setup.
        """
        if not self.channel_present():
            if self.creds_present() and self.user_present():
                channel = self._api().channel(self.settings.twitch_broadcaster_id)
                if not channel:
                    OBS.error('Twitch channel info fetch failed.')

                    return ''

                OBS.info('Twitch OAuth channel info fetch succeeded.')
                self.channel_set(channel['broadcaster_name'])

        return self.settings.twitch_channel

    #----------------------------------------------------------------------
    def channel_set(self, channel: str) -> bool:
        self.settings.twitch_channel = channel

        return True

    #----------------------------------------------------------------------
    def clear_settings(self) -> None:
        self.settings.twitch_oauth_access_token = ''
        self.settings.twitch_oauth_expiry_at = 0

        self.settings.twitch_broadcaster_id = -1
        self.settings.twitch_username = ''
        self.settings.twitch_channel = ''

        self.api = None

    #----------------------------------------------------------------------
    def _api(self) -> TwitchApi | False:
        if not self.creds_present():
            OBS.warn('Attempted TwitchApi access without creds set first.')
            return False

        if not self.api:
            self.api = TwitchApi(
                self.APP_CLIENT_ID,
                self.settings.twitch_oauth_access_token
            )

        return self.api


###########################################################################
# Twitch IRC Client
###########################################################################

class TwitchIRCClient:
    """
    The IRC client has two responsibilities.

    1. Recieving messages:

        socket > IRC parsing > ChatMessage objects > BRBScript command parser

    2. Sending messages:

        BRBScript > IRC parsing >socket

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

        self._create_wakeup_pair()

    #----------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return

        self._drain_wakeup()

        self.running = True
        self.thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name=f"{SCRIPT_NAME}-TwitchIRC",
        )

        OBS.debug("Starting IRC thread.")
        self.thread.start()

    #----------------------------------------------------------------------
    def stop(self) -> None:
        self.running = False

        try:
            if self.wakeup_writer:
                self.wakeup_writer.send(b"\x00")
        except Exception as e:
            OBS.debug(f"Failed to wake IRC thread: {e!r}")

        try:
            if self.socket:
                OBS.debug("Closing irc socket.")
                self.socket.shutdown(socket.SHUT_RDWR)
                self.socket.close()
                #self.thread.join() # wait for the thread to terminate.
            if self.thread and self.thread.is_alive():
                OBS.debug("Stopping background thread.")

        except Exception as e:
            OBS.debug(f"Failed to close socket: {e!r}")

    #----------------------------------------------------------------------
    def close(self):
        self.stop()

        for sock in (self.wakeup_reader, self.wakeup_writer):
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass

        self.wakeup_reader = None
        self.wakeup_writer = None
        self.socket = None

    #----------------------------------------------------------------------
    def send_chat(self, message: str) -> None:
        self._send_raw(f"PRIVMSG #{self.channel} :{message}")

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def _create_wakeup_pair(self):
        self.wakeup_reader, self.wakeup_writer = socket.socketpair()

   #----------------------------------------------------------------------
    def _drain_wakeup(self):
        self.wakeup_reader.setblocking(False)

        try:
            while self.wakeup_reader.recv(4096):
                pass
        except BlockingIOError:
            pass
        finally:
            self.wakeup_reader.setblocking(True)

   #----------------------------------------------------------------------
    def _connect(self) -> None:
        """
        Establish an SSL-wrapped socket connection.

        Ref: https://stackoverflow.com/a/23615951/70876
        """
        OBS.debug("Connecting to irc...")
        raw = socket.create_connection((self.HOST, self.PORT), timeout=60)

        ctx = ssl.create_default_context()
        self.socket = ctx.wrap_socket(
            raw,
            server_hostname=self.HOST,
        )
        self.socket.setblocking(True)

        # Ref: https://dev.twitch.tv/docs/chat/irc
        self._send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send_raw(f"PASS oauth:{self.oauth}")
        self._send_raw(f"NICK {self.username}")
        self._send_raw(f"JOIN #{self.channel}")

        OBS.debug("connected.")

    #----------------------------------------------------------------------
    def _receive_loop(self) -> None:
        OBS.debug("Starting _receive_loop.")

        while self.running:
            try:
                self._connect()
                buffer = b""

                while self.running:
                    readable, _, _ = select.select(
                        [self.socket, self.wakeup_reader],
                        [],
                        [],
                    )

                    if self.wakeup_reader in readable:
                        self.wakeup_reader.recv(1)
                        break

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

            except ConnectionError:
                pass

            except Exception as ex:
                OBS.warn(f"Twitch IRC Listener: {ex!r}")

                try:
                    if self.socket:
                        OBS.debug("Closing socket.")
                        self.socket.close()
                except Exception as e:
                    OBS.debug(f"Failed to close socket: {e!r}")

                if self.running:
                    OBS.debug(
                        f"Sleeping for {self.RECONNECT_DELAY} secs."
                    )
                    time.sleep(self.RECONNECT_DELAY)

            finally:
                if self.socket:
                    try:
                        self.socket.close()
                    except OSError:
                        pass

                self.socket = None

    #----------------------------------------------------------------------
    def _handle_line(self, line: str) -> None:
        if line.startswith("PING"):
            OBS.debug(f"Answering PING with PONG.")
            self._send_raw(line.replace("PING", "PONG", 1))
            return

        if "NOTICE * :Login unsuccessful" in line:
            OBS.debug("IRC auth rejected.")
            self.stop()
            return

        if " PRIVMSG " not in line:
            #OBS.debug(f"Not a privmsg-- ignoring.")
            return

        OBS.debug(f"Processing privmsg: {line}")
        msg = self._parse_privmsg(line)
        #OBS.debug(f"Parsed message: {msg}")
        if msg:
            OBS.debug(f"Triggering callback for: {msg}")
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

        return ChatMessage(
            username,
            tags.get("display-name", username),
            message[1:],
            tags.get("mod") == "1",
            "broadcaster/1" in tags.get("badges", ""),
        )

    #----------------------------------------------------------------------
    def _send_raw(self, line: str) -> None:
        with self.send_lock:
            OBS.debug(f"Sending line: {line}")
            self.socket.sendall((line + "\r\n").encode("utf-8"))

    #----------------------------------------------------------------------
    @staticmethod
    def _parse_tags(raw: str) -> Dict[str, str]:
        result = {}
        for field in raw.split(";"):
            if "=" in field:
                k, v = field.split("=", 1)
                result[k] = v

        return result


###########################################################################
# Chat Message Data Container
###########################################################################

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


###########################################################################
# Guess Manager
###########################################################################

class GuessManager:
    """
    Manages updates to any active brb guessing.

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
        | auto-hide timer expires
        V
    IDLE

    manager = GuessManager()
    manager->start()                      # Allow guesses.
    manager->add_guess(username, secs)
    manager->add_guess(diff_user, diff_secs)
    manager->end(auto_hide_delay_secs)    # Block guesses.
    manager->winner()                     # Return last winner, till reset.
    manager->clear()                      # Reset back to defaults.
    """

    #----------------------------------------------------------------------
    def __init__(self):
        self.clear()

    #----------------------------------------------------------------------
    def clear(self) -> None:
        self.active: bool = False

        self.start_time: Optional[datetime.datetime] = None
        self.stop_time: Optional[datetime.datetime] = None
        self.auto_hide_time: Optional[datetime.datetime] = None

        self.guesses: Dict[str, int] = {}

        self._winner: Optional[str] = None
        self.actual_secs: Optional[int] = None

    #----------------------------------------------------------------------
    def running(self) -> bool:
        return self.active

    #----------------------------------------------------------------------
    def start(self) -> None:
        self.active = True

        self.start_time = DT.now()
        self.stop_time = None
        self.auto_hide_time = None

        self.guesses = {}

        self._winner = None
        self.actual_secs = None

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
    def elapsed_time(self) -> datetime.timedelta | None:
        """
        Handles the time logic for the difference between the recorded
        start_time and "now".

        Used by the TimerRenderer to show the on-screen count-up timer
        every second.

        Returns None if no !brb has been started.

        Returns the paused time after .end() has been called but before
        .clear() has been called.
        """
        if self.start_time is None:
            return None

        if self.active:
            # Finish time is ongoing.
            finish_time = DT.now()
        elif self.stop_time is not None:
            # Finish time is stored from last !back.
            finish_time = self.stop_time
        else:
            return None

        #OBS.debug(locals())
        elapsed = finish_time - self.start_time

        return elapsed

    #----------------------------------------------------------------------
    def timer_str(self) -> str:
        """
        Consistent formatting for the on-screen timer.

        Returns the default placeholder string when a !brb isn't active.
        """
        elapsed = self.elapsed_time()
        if elapsed is None:
            return DEFAULTS.TIMER_TEXT

        return DT.duration_str(elapsed)

    #----------------------------------------------------------------------
    def end(self, auto_hide_secs: int) -> None:
        # Handle a stream ending with no !brb's having been run.
        if not self.active:
            return

        # Otherwise shut down cleanly.
        self.active = False

        self.stop_time = DT.now()
        self.actual_secs = (self.stop_time - self.start_time).total_seconds()
        self.auto_hide_time = self.stop_time + DT.secs_to_delta(auto_hide_secs)

    #----------------------------------------------------------------------
    def winner(self) -> tuple[str, int] | tuple[False, None]:
        """
        Returns false if no !brb has run, or is still running, or if
        nobody registered any guesses.

        Returns a tuple of (username, guessed_secs) when a winner is
        present.
        """
        if self.active:
            return (False, None) # No winner when brb is still active.

        if len(self.guesses) == 0:
            return (False, None) # Nobody guessed, so there's no winner.

        qualified = self._qualified()

        if not qualified:
            return (False, None)

        username, guessed_secs = next(reversed(qualified.items()))

        return (username, guessed_secs)

    #----------------------------------------------------------------------
    def should_hide(self) -> bool:
        """
        Takes the configured auto-hide delay into account after a !back
        command to determine when the on-screen timer should be hidden.

        Returns True **unless** we're in either an active !brb, or the
        cooldown period after a !back command.
        """
        if self.active:
            return False

        if self.auto_hide_time is not None:
            return DT.now() >= self.auto_hide_time

        return True

    #----------------------------------------------------------------------
    def _qualified(self) -> Dict[str, int]:
        if self.actual_secs is None:
            return {}

        # Exclude any guess larger than the actual seconds.
        qualified = {
            username: seconds
            for username, seconds in self.guesses.items()
            if seconds <= self.actual_secs
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


###########################################################################
# Timer Renderer
###########################################################################

class TimerRenderer:
    """
    Handles the display and contents of the  OBS on-screen timer.
    """

    #----------------------------------------------------------------------
    def __init__(self, text_source_name: str):
        self.set_source(text_source_name)

    #----------------------------------------------------------------------
    def set_source(self, text_source_name: str) -> None:
        self.text_source_name = text_source_name

    #----------------------------------------------------------------------
    def set_text(self, text: str) -> bool:
        """
        Update the on-screen timer with the provided text.
        """
        if not self.text_source_name:
            OBS.error(f"No text_source_name is set. Call .set_source(text_source_name)")
            return False

        with OBS.source_by_name(self.text_source_name) as source:
            if source is None:
                OBS.error(
                    f"Couldn't find source named '{self.text_source_name}'."
                )
                return False

            OBS.source_update(source, {'text': text})
            OBS.debug(
                "Set timer text source "
                f"'{self.text_source_name}' to '{text}'."
            )

        return True

    #----------------------------------------------------------------------
    def clear(self) -> None:
        """
        Reset the on-screen timer text source.
        """
        self.set_text('')
        self.hide()

    #----------------------------------------------------------------------
    def show(self) -> None:
        self._set_visibility(True)

    #----------------------------------------------------------------------
    def hide(self) -> None:
        self._set_visibility(False)

    # ---------------------------------------------------------------------
    # Internal OBS Helpers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def _set_visibility(self, visibility: bool) -> None:
        """
        Set the visibility of the sceneitem associated with the
        configured text source.
        """
        try:
            with OBS.scene_current() as scene:
                with OBS.sceneitem_by_name(self.text_source_name, scene) as si:
                    OBS.sceneitem_set_visible(si, visibility)
        except Exception as exc:
            OBS.error(f"Failed to set sceneitem visibility: {exc!r}")



###########################################################################
# Script Settings Storage
###########################################################################

@dataclass
class BRBSettings(object):
    """
    Holds the script's internal settings/config data.

    Also acts as a runtime wrapper for the data that we need to have
    persisted by OBS between launches.

    This class does NOT need to store anything from `script_properties()`
    since OBS already handles that. This class is for values not intended
    to be exposed to the user.

    Typing is critical here to ensure our to_data() method calls the
    right obs module method.
    """
    twitch_oauth_access_token: str = ''
    twitch_oauth_expiry_at: int = 0

    twitch_broadcaster_id: int = 0
    twitch_username: str = ''
    twitch_channel: str = ''

    auto_hide_secs: int = DEFAULTS.AUTO_HIDE_SECS

    #----------------------------------------------------------------------
    def __setattr__(self, name: str, value: any) -> any:
        """
        Prevent the addition of arbitrary new attributes.

        This means trying to set `settings.some_new_attr = 'value'` will
        raise an AttributeError.
        """
        if not hasattr(self, name):
            raise AttributeError(f"Invalid setting '{name}'.")

        object.__setattr__(self, name, value)
        return value

    #----------------------------------------------------------------------
    def from_data(self, data): # data: obs_data_t
        incoming = OBS.data_get_all(data)
        attrs = self.__attrs()
        for name, value in incoming.items():
            if name in attrs:
                #OBS.debug(f"Importing {name} = {value}")
                setattr(self, name, value)

    #----------------------------------------------------------------------
    def to_data(
            self,
            existing, # = None, # obs_data_t
        ): # -> obs_data_t
        """
        Dump all local BRBSettings attributes to the provided obs_data_t
        object.
        """
        for name in self.__attrs():
            value = getattr(self, name)
            #OBS.debug(f"Setting {name} = {value}")
            OBS.data_set(
                existing, # if existing is not None else self.__obs_data,
                name,
                value,
            )

    #----------------------------------------------------------------------
    def to_json(self) -> str:
        out = {}
        for name in self.__attrs():
            value = getattr(self, name)
            out[name] = value

        return json.dumps(out)

    #----------------------------------------------------------------------
    def __attrs(self) -> list[str]:
        return [
            attr for attr in vars(self)
            if not callable(getattr(self, attr)) and not attr.startswith("_")
        ]


###########################################################################
# Main Controller
###########################################################################

class BRBScript:
    """
    This class serves as the script's main controller.

    It provides methods for the OBS hook functions to call, in order to
    keep all business logic consolidated here.

    Classes used by BRBScript are expected not to use any method from
    the `obs` module unless that's their sole purview. This is mostly
    accomplished with dependency injection-- passing in handlers and
    callbacks to the classes.

    Architecture:

        BRBScript ---------------------------------------------|
            |          v        |        v         |           |
            |     BRBSettings   | SourceGenerator  |           |
            v                   v                  v           v
        TwitchOauth       TwitchIRCClient     GuessManager  TimerRenderer
            |                   |
            v                   v
         TwitchApi         ChatMessage

    Ref: https://obsproject.com/kb/scripting-guide#script-lifecycle
    """

    # ---------------------------------------------------------------------
    # Data Management
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def __init__(self, obs_module):
        self.obs = obs_module
        self.reset()

    #----------------------------------------------------------------------
    def reset(
        self,
        settings = None, # obs_data_t
    ) -> None:
        self.settings: BRBSettings = BRBSettings()
        if settings:
            self.settings.from_data(settings)

        self.twitch: TwitchOAuth = TwitchOAuth(self.settings)
        self.guesses: GuessManager = GuessManager()
        self.renderer: Optional[TimerRenderer] = None # Gets created when streaming starts.

        # This is initialized on an as-needed basis.
        self.irc: Optional[TwitchIRCClient] = None

        # Flipped on when on_obs_ready() completes successfully.
        self.frontend_ready: bool = False

        # Store registered event handlers.
        # Ensures a previously-unaccessed dict key starts out as an empty list.
        self.events = defaultdict(list)

        self.send_lock = threading.Lock()

    #----------------------------------------------------------------------
    def settings_merge(
        self,
        new_settings, # obs_data_t
    ) -> None:
        """
        Sync settings both directions.

        new_settings from OBS get loaded into BRBSettings.

        All BRBSettings get written back to new_settings, which
        is OBS's internal storage for this script.
        """
        self.settings.from_data(new_settings)
        self.settings.to_data(new_settings) # new_settings is modified by this call.


    # ---------------------------------------------------------------------
    # State Introspection
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def is_frontend_ready(self) -> bool:
        return self.frontend_ready

    #----------------------------------------------------------------------
    def is_source_exists(self, source_name: str = None) -> bool:
        return (
            self.is_frontend_ready()
            and OBS.source_exists(
                SOURCES.TEXT_TIMER if source_name is None else source_name,
            )
        )

    #----------------------------------------------------------------------
    def is_renderer_initialized(self) -> bool:
        return self.renderer is not None

    #----------------------------------------------------------------------
    def is_renderer_visible(self) -> bool:
        return self.is_renderer_initialized() and self.renderer.visible()

    #----------------------------------------------------------------------
    def is_guessing_initialized(self) -> bool:
        return self.guesses is not None

    #----------------------------------------------------------------------
    def is_guessing_running(self) -> bool:
        return self.is_guessing_initialized() and self.guesses.running()

    #----------------------------------------------------------------------
    def is_irc_initialized(self) -> bool:
        return self.irc is not None

    #----------------------------------------------------------------------
    def is_irc_running(self) -> bool:
        return self.is_irc_initialized() and self.irc.running

    #----------------------------------------------------------------------
    def is_irc_connected(self) -> bool:
        return self.is_irc_running() and self.irc.thread.is_alive()
        # TODO: Should also check for socket connection. Ref: https://stackoverflow.com/a/62277798/70876

    #----------------------------------------------------------------------
    def is_twitch_initialized(self) -> bool:
        return self.twitch is not None

    #----------------------------------------------------------------------
    def is_twitch_present(self) -> bool:
        return self.is_twitch_initialized() and self.twitch.creds_present()

    #----------------------------------------------------------------------
    def is_twitch_valid(self) -> bool:
        return self.is_twitch_present() and not self.twitch.creds_expired()

    #----------------------------------------------------------------------
    def is_twitch_populated(self) -> bool:
        return (
            self.is_twitch_valid()
            and self.twitch.user_present()
            and self.twitch.channel_present()
        )

    #----------------------------------------------------------------------
    def is_timer_ticking(self) -> bool:
        return OBS.timer_running(self.on_timer)


    # ---------------------------------------------------------------------
    # State Mutation
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def update_prop_visibility(
        self,
        props, # obs_properties_t
    ) -> bool:
        all = {
            # widget_name: boolean to show or not
            'twitch_connect_button': True,
            'twitch_oauth_access_token': True,
            'twitch_creds_expired': not self.is_twitch_valid(),
            'auto_hide_secs': True,

            'make_ready_button': not self.is_frontend_ready(),
            'make_not_ready_button': self.is_frontend_ready(),
            'create_source_button': not self.is_source_exists(),
            'irc_start_button': not self.is_irc_connected(),
            'irc_stop_button': self.is_irc_connected(),
            'guess_start_button': not self.is_guessing_running(),
            'guess_stop_button': self.is_guessing_running(),
            'timer_start_button': not self.is_timer_ticking(),
            'timer_stop_button': self.is_timer_ticking(),
        }
        OBS.properties_visibility_set(props, all)
        return True

    #----------------------------------------------------------------------
    def update_twitch(self, changes: Dict[str, any] = None) -> bool:
        """
        Make Twitch API queries when we get a new access token.

        token unchanged: no op
        token not empty -> token empty: remove local settings
        token was empty -> token not empty: make api calls with new token
        token not empty -> different token: make api calls with new token
        """
        if (
            changes is None
            or 'twitch_oauth_access_token' not in changes.keys()
            or changes['twitch_oauth_access_token'].strip() == self.settings.twitch_oauth_access_token
        ):
            OBS.debug(f"No effective change to twitch_oauth_access_token.")
            return self.settings.twitch_oauth_access_token != ''

        new_token = changes['twitch_oauth_access_token'].strip()
        if (
            len(new_token) == 0
            and len(self.settings.twitch_oauth_access_token) > 0
        ):
            self.twitch.clear_settings()
            OBS.warn('Stored Twitch credentials cleared.')
            return False

        if (len(new_token) > 0):
            # Buy just enough time to run validate().
            self.twitch.creds_set(new_token, DT.timestamp() + 120)

        # Check for Twitch OAuth creds.
        if not self.twitch.creds_present():
            OBS.warn('No Twitch credentials available yet.')
            return False

        # Validate token and check for a fetched Twitch user.
        if not self.twitch.user_get():
            OBS.error('Twitch OAuth token validation failed.')
            return False

        # Check for a fetched Twitch channel.
        if not self.twitch.channel_get():
            OBS.error('Twitch channel info fetch failed.')
            return False

        OBS.info('Twitch credentials validated.')
        return True

    #----------------------------------------------------------------------
    def update_renderer(self, source_name: str) -> bool:
        """
        Tell the renderer to target a different text source if the user
        changed it.
        """
        if not self.is_frontend_ready():
            return False

        if (
            self.renderer is not None
            and self.is_source_exists(source_name)
        ):
            self.renderer.clear()
            self.renderer.set_source(source_name)

        self.renderer = TimerRenderer(source_name)

        return True

    # ---------------------------------------------------------------------
    # Event Management
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def event_router(
        self,
        obs_const: int, # OBS_FRONTEND_EVENT_*
    ) -> None:
        """
        After OBS.event_register_router() has been called, this method
        is invoked by OBS whenever an event is fired.

        This router is only responsible for determining whether to
        respond to an event (and triggering any callbacks, if so), or
        to ignore it.
        """
        if (
            obs_const in self.events.keys()
            and len(self.events[obs_const]) > 0
        ):
            OBS.debug(
                f"Responding to '{OBS.event_name(obs_const)}' event with "
                f"{len(self.events[obs_const])} handler(s)."
            )

            # Run all callbacks.
            [x() for x in self.events[obs_const]]

    #----------------------------------------------------------------------
    def event_add(
        self,
        obs_const: int, # OBS_FRONTEND_EVENT_*
        callback: ObsEventCallback,
    ) -> None:
        """
        Register a callback for a specific OBS event.

        The calling context MUST have already called
        OBS.event_register_router()
        """
        self.events[obs_const].append(lambda: callback())
        OBS.debug(
            f"Event listener registered for event {OBS.event_name(obs_const)}: {callback.__name__}"
        )

    #----------------------------------------------------------------------
    def event_start_ticking(self) -> None:
        """
        Handles a sucessful !brb chat command by registering an OBS
        "timer" that fires once every second to update the on-screen
        text source's display count-up time.
        """
        self.event_stop_ticking() # Clear any existing timer.
        try:
            OBS.timer_add(self.on_timer, 1 * 1000) # ms
        except ValueError:
            OBS.warn('Tried to start timer but it was already running.')

    #----------------------------------------------------------------------
    def event_stop_ticking(self) -> None:
        try:
            OBS.timer_remove(self.on_timer, True)
        except ValueError:
            OBS.warn("Tried to remove timer but it wasn't running.")


    # ---------------------------------------------------------------------
    # Event Handlers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    # ✅
    def on_props(self): # -> obs_properties_t
        """
        Handles defining the GUI properties for the script.

        Must be stateless and without conditionals. The modified
        callbacks will be responsible for showing/hiding any counterpart
        widgets.
        """
        factory = OBSPropsFactory(obs, OBS.properties_create())

        factory.twitch_button(
            'connect', #  -> button id = `twitch_connect_button`. No callback because URL is present (but would be `BRBScript.on_twitch_connect`.)
            TwitchOAuth.kickoff_url(),
            self.obs.OBS_TEXT_INFO_NORMAL,
            (
                'Open a browser window to obtain '
                'a Twitch API token for this script to use. '
                'Paste it below.'
            ),
        )

        factory.text(
            "twitch_oauth_access_token",
            "Twitch OAuth Access Token",
            self.obs.OBS_TEXT_PASSWORD,
            self.on_twitch_oauth_access_token
        )

        #     factory.text_info( # TODO text_info() doesn't exist yet
        #         'twitch_creds_expired',
        #         'Twitch token has expired. Please obtain a fresh token.',
        #         self.obs.OBS_TEXT_INFO_WARNING,
        #     )

        factory.int_with_unit(
            "auto_hide_secs",
            "Auto-hide Delay (seconds)",
            0,       # min
            60 * 30, # max (30 mins)
            1,       # step
            " secs", # unit
            self.on_auto_hide_secs
        )

        return factory.props

    #----------------------------------------------------------------------
    # ✅
    def on_load(
        self,
        settings, # obs_data_t
    ) -> None:
        """
        Called from script_load() during first OBS initialization.

        The OBS frontend won't be loaded yet, so all we need to do is
        register our various event handlers.
        """
        self.frontend_ready = False
        self.reset(settings)

        OBS.event_register_router(self.event_router)

        self.event_add(
            self.obs.OBS_FRONTEND_EVENT_FINISHED_LOADING,
            self.on_obs_ready,
        )
        self.event_add(
            self.obs.OBS_FRONTEND_EVENT_STREAMING_STARTING,
            self.on_streaming_starting,
        )
        self.event_add(
            self.obs.OBS_FRONTEND_EVENT_STREAMING_STOPPING,
            self.on_streaming_stopping,
        )

        OBS.debug(f"on_load settings: {OBS.data_get_json(settings)}")

    #----------------------------------------------------------------------
    # ✅
    def on_obs_ready(self) -> None:
        """
        Handles the OBS "frontend finished loading" event.

        Once the OBS frontend is ready, we can check for the timer text
        source, verify oauth creds, and any other startup tasks.

        Does not fire if the script is re-loaded in the GUI. Only runs
        on OBS startup.
        """
        self.frontend_ready = True

        # Create the text source if it doesn't already exist.
        if not self.is_source_exists(SOURCES.TEXT_TIMER):
            OBS.debug(
                "Creating on-screen timer text source "
                f"named: {SOURCES.TEXT_TIMER}"
            )
            self.on_create_source()

        OBS.info(f"{SCRIPT_NAME} ready!")

    #----------------------------------------------------------------------
    # ✅
    def on_update(
        self,
        changed_settings, # obs_data_t
    ) -> None:
        """
        Fired whenever the script's GUI properties are edited, or
        something changes in OBS (such as activating a scene or making
        a source visibile.)

        Responsible for propagating OBS's settings into this script's
        runtime state.

        Avoid side-effects here-- leave that to the individual
        `obs_property_set_modified_callback()` handlers.

        Can't affect GUI widgets directly, only stored data.
        """
        #OBS.debug(f"changed settings: {OBS.data_get_json(changed_settings)}")
        incoming = OBS.data_get_all(changed_settings)

        self.update_twitch(incoming)
        #OBS.debug(f"BRBSettings after update_twitch: {self.settings!s}")

        # Write our tweaked settings values back to the provided
        # `settings` obs_data_t object for OBS to persist for us.
        self.settings.to_data(changed_settings)
        #OBS.debug(f"changed settings after update_twitch: {OBS.data_get_json(changed_settings)}")

        return True

    #----------------------------------------------------------------------
    # ✅
    def on_create_source(
        self,
        props = None, # obs_properties_t
        prop = None, # obs_property_t
    ) -> bool:
        # if prop is not None:
        #     source = self.settings.source_name
        # else:
        source = SOURCES.TEXT_TIMER

        if not self.is_frontend_ready():
            OBS.warn("OBS frontend is not yet ready. Can't create text source.")
            return False

        if self.is_source_exists(source):
            OBS.info(f"Source already exists: {source}")
            return True

        generator = SourceGenerator(
            OBS,
            source_name = source,
            text = DEFAULTS.TIMER_TEXT,
        )
        if generator.create_source():
            OBS.frontend_open_source_props(source)
            OBS.info(
                f"Created new Timer Text Source '{source}' successfully.",
            )
            return True

        OBS.error(
            f"Failed to create new Timer Text Source '{source}'.",
        )
        return False

    #----------------------------------------------------------------------
    #
    def on_streaming_starting(self) -> None:
        """
        Script "main loop". Start up the IRC client to listen for chat
        commands.
        """
        # Assume the frontend is ready if we're starting to stream.
        self.frontend_ready = True

        # Check creds. Bail out without twitch user/channel info.
        if not self.update_twitch():
            OBS.warn("No Twitch credentials saved. Skipping Timer Source check and IRC bot startup.")
            return

        # Prep on-screen timer.
        if self.update_renderer(SOURCES.TEXT_TIMER):
            OBS.debug('On-screen timer hidden.')
            self.renderer.hide()

        # Start the IRC client.
        self.irc = TwitchIRCClient(
            self.twitch.channel_get(),
            self.twitch.user_get()['name'],
            self.settings.twitch_oauth_access_token,
            self.on_chat,
        )
        self.irc.start()
        OBS.info(f"{SCRIPT_NAME} chat bot started.")

        # TODO: show/hide prop buttons? Can't do that here cause they're not args.

    #----------------------------------------------------------------------
    #
    def on_timer(self) -> None:
        """
        Scheduled to run every second while a brb is active.

        Updates the on-screen timer.
        """
        OBS.debug('Timer triggered.')
        if self.guesses.should_hide():
            OBS.debug("should_hide is true- removing self")
            if self.is_renderer_initialized():
                self.renderer.hide()
            OBS.event_remove_self()

        timer_text = self.guesses.timer_str()
        OBS.debug(f"Setting on-screen text to {timer_text}")
        self.renderer.set_text(timer_text)

    #----------------------------------------------------------------------
    #
    def on_streaming_stopping(self) -> None:
        """
        Shutdown the background IRC client thread and reset script state.
        """
        # Stop guesses immediately.
        self.guesses.end(0)

        # Hide the on-screen timer.
        self.event_stop_ticking()
        # if self.renderer is not None:
        #     self.renderer.hide()

        # Shut down the IRC client.
        if self.is_irc_running():
            self.irc.stop()

    #----------------------------------------------------------------------
    #
    def on_unload(self) -> None:
        self.frontend_ready = False

        OBS.event_unregister_router(self.event_router)
        self.event_stop_ticking()
        self.events = {}

        if self.renderer is not None:
            self.renderer.hide()
            self.renderer.set_text(DEFAULTS.TIMER_TEXT)

        if self.irc is not None and self.irc.running:
            self.irc.stop()


    # ---------------------------------------------------------------------
    # OBS Widget Modified Callbacks
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def on_twitch_oauth_access_token(
        self,
        props, # obs_properties_t
        prop, # obs_property_t
        settings = None, # obs_data_t
    ) -> bool:
        #new_token = OBS.data_get(settings, 'twitch_oauth_access_token', 'uh oh')
        #OBS.debug(f"all settings: {OBS.data_get_json(settings)}")
        #OBS.debug(f"Updating oauth token to: {new_token}")
        self.update_twitch(OBS.data_get_all(settings))

        self.update_prop_visibility(props)
        return True

    #----------------------------------------------------------------------
    def on_auto_hide_secs(
        self,
        props, # obs_properties_t
        prop, # obs_property_t
        settings, # obs_data_t
    ) -> bool:
        self.settings.auto_hide_secs = int(OBS.data_get(settings, 'auto_hide_secs'))
        #self.update_prop_visibility(props)
        return False # GUI is already up to date.

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
            case _:
                OBS.debug('No BRB commands matched. Skipping.')


    # ---------------------------------------------------------------------
    # Chat Command Callbacks
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def command_brb(self, msg: ChatMessage) -> None:
        """
        Handle a !brb command.
        """
        OBS.debug("Handling !brb.")
        # Validate command was sent by broadcaster or nod.
        if not (msg.is_mod or msg.is_broadcaster):
            OBS.debug("Only mods can start brb.")
            self.irc.send_chat(MESSAGES.ONLY_MOD_START)
            return

        if self.guesses.running():
            OBS.debug("brb already running.")
            self.irc.send_chat(MESSAGES.ALREADY_RUNNING)
            return

        # Remove any leftover timers if this brb was started within a
        # previous auto-hide delay.
        OBS.debug("Stopping any previous stray tickers.")
        self.event_stop_ticking()

        # Open up guessing.
        OBS.debug("Opening up guessing.")
        self.guesses.start()

        # # Schedule 1 second timer updates.
        OBS.debug("Starting on-screen ticker.")
        self.event_start_ticking()

        # # Show the on-screen timer.
        # OBS.debug("Enabling on-screen timer visibility.")
        # self.renderer.show()

        # # Send the starting chat message.
        # self.irc.send_chat(str.format(
        #     MESSAGES.BRB_STARTED,
        #     streamer = self.settings.twitch_username,
        # ))
        OBS.debug("brb handled.")

    #----------------------------------------------------------------------
    def command_at(self, msg: ChatMessage) -> None:
        """
        Handle an !at command.
        """
        OBS.debug("Handling !at.")
        if not self.guesses.running():
            OBS.debug("No brb running.")
            self.irc.send_chat(MESSAGES.NO_BRB)
            return

        parsed_delta = self._parse_at(msg.message)
        if not parsed_delta:
            OBS.error(f"Failed to parse !at in message: {msg}")
            self.irc.send_chat(str.format(
                MESSAGES.BAD_GUESS,
                user = msg.username,
            ))
            return #

        existing_guess = self.guesses.has_guess(msg.username)
        if existing_guess:
            OBS.warn(f"User {msg.username} already has a guess registered: {existing_guess}")
            self.irc.send_chat(str.format(
                MESSAGES.ALREADY_GUESSED,
                user = msg.username,
                guess = DT.strfdelta(existing_guess),
            ))
            return

        if self.guesses.add_guess(msg.username, parsed_delta.total_seconds()):
            OBS.debug(f"Guess registered for user {msg.username}: {parsed_delta}")
            self.irc.send_chat(str.format(
                MESSAGES.GUESS_ACCEPTED,
                user = msg.username,
                guess = DT.strfdelta(parsed_delta),
            ))

        OBS.debug("!at handled.")

    #----------------------------------------------------------------------
    def command_back(self, msg: ChatMessage) -> None:
        """
        Handler that's called when self.irc returns a ChatMessage with
        a !back command.
        """
        OBS.debug("Handling !back.")
        # Validate command was sent by broadcaster or nod.
        if not (msg.is_mod or msg.is_broadcaster):
            OBS.debug("Only mods can run !back.")
            self.irc.send_chat(MESSAGES.ONLY_MOD_END)
            return

        if not self.guesses.running():
            OBS.debug("No brb running.")
            self.irc.send_chat(MESSAGES.NO_BRB)
            return

        # Disallow further guessing.
        OBS.debug("Stopping guesses.")
        self.guesses.end(self.settings.auto_hide_secs)

        # Announce the winner.
        (winner, guess_secs) = self.guesses.winner()
        if winner:
            OBS.debug(f"!brb winner is: {winner}")
            self.irc.send_chat(str.format(
                MESSAGES.BRB_FINISHED,
                streamer = self.settings.twitch_username,
                time = DT.duration_str(DT.duration_secs_to_dt(self.guesses.elapsed_time().total_seconds())),
                winner = winner,
                diff = DT.duration_str(self.guesses.actual_secs - guess_secs),
            ))
        else:
            OBS.debug("No guesses, so no winner.")
            self.irc.send_chat(str.format(
                MESSAGES.BRB_FINISHED_NO_GUESSES,
                streamer = self.settings.twitch_username,
                time = DT.duration_str(self.guesses.actual_secs),
            ))
        OBS.debug("!back handled.")

    # ---------------------------------------------------------------------
    # Internal Helpers
    # ---------------------------------------------------------------------

    #----------------------------------------------------------------------
    def _parse_at(self, msg: str) -> datetime.timedelta | False:
        """
        Ref: timedelta https://stackoverflow.com/a/51916936/70876
        Ref: regex https://stackoverflow.com/a/8318367/70876
        """
        [_cmd, time_str, *_rest] = msg.split()
        delta = DT.strpdelta(time_str)
        if not delta:
            OBS.warn('Could not parse !at command argument: %s' % (msg))
            return False

        return delta


###########################################################################
# OBS Script API
###########################################################################

###########################################################################
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

        <p>The script adds a text Source named <code>{SOURCES.TEXT_TIMER}</code> to your active Scene. Modify as desired, but <i>leave the name untouched</i>. Connects to your Twitch chat to listen for the BRB commands listed above.</p>
        -->

        <p><i>Originally written exclusively for <a href="https://www.twitch.tv/enns">Enns</a> by <a href="https://github.com/beporter">beporter</a> in August 2026.</i></p>
    """.strip())

###########################################################################
def script_defaults(
    settings, # obs_data_t
): # -> obs_data_t
    """
    This lifecycle methods is called EARLY in the script's startup
    process. Before `script_properties()` is even called for the first
    time.
    """
    OBS.data_set_default(
        settings,
        "auto_hide_secs",
        DEFAULTS.AUTO_HIDE_SECS,
    )

###########################################################################
def script_properties() -> None: # -> obs_properties_t
    """
    Tells OBS what GUI controls to expose for this script.
    """
    props = script.on_props()
    if props is not None:
        OBS.debug(f"{SCRIPT_NAME} properties generated.")
    else:
        OBS.debug(f"{SCRIPT_NAME} property generation failed.")

    return props


###########################################################################
def script_load(
    settings, # obs_data_t
) -> None:
    """
    Called once when the script is loaded.
    """
    script.on_load(settings)

    OBS.debug(f"{SCRIPT_NAME} loaded.")

###########################################################################
def script_update(
    settings, # obs_data_t
) -> None:
    """
    Called by OBS when the user has made modifications to the script's
    configuration in the GUI.

    This method is only responsible for receiving OBS state (in
    `settings`) and propagating it to this script's runtime state.
    This is necessary because the OBS state isn't accessible to the
    script anywhere but in a few of these callbacks.

    Ref: https://docs.obsproject.com/scripting#script_update
    """
    script.on_update(settings)

    OBS.debug(f"{SCRIPT_NAME} settings updated.")

###########################################################################
def script_save(
    settings, # obs_data_t
) -> None:
    """
    Called before OBS saves the script settings to the OBS user's local
    storage.
    """
    script.settings_merge(settings)

    OBS.debug(f"{SCRIPT_NAME} settings saved.")

###########################################################################
def script_unload() -> None:
    """
    Called when OBS unloads the script.
    """
    script.on_unload()

    OBS.debug(f"{SCRIPT_NAME} unloaded.")

###########################################################################
# Global Script Instance
###########################################################################

script = BRBScript(obs) # Must _always_ exist in module/global namespace.

if __name__ == "__main__":
    sys.exit(
        "This script can't be run directly. "
        "Add it to OBS in the Tools > Scripts panel."
    )
