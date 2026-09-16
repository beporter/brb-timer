"""
Minimal implementation of BRB Timer, starting from working timer example in simple.py.
"""
import obspython as S
import datetime
import inspect
import sys
import time
from typing import Callable

###########################################################################
# Events class
class Events:
    source_name: str = ''
    running: bool = False
    start_time: int = 0
    stop_time: int = 0
    hide_time: int = 0
    #guesses: dict[str, int] = {}

    #----------------------------------------------------------------------
    def on_list_modified(self, props, prop, settings = None):
        """
        Our callback that's fired from a GUI prop modification to
        determine if GUI redraw is needed. Return True to tell OBS to
        redraw the property widgets.
        """
        debug(
            f"entering on_list_modified. "
            f"running is {self.running!s}. "
            f"prop is {S.obs_property_name(prop)}"
        )

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
    def on_start_button(self, props, prop, settings = None):
        """
        Callback fired from GUI button click.
        """
        if self.source_name:
            self.running = True
            self.start_time = int(time.time())
            self.stop_time = 0
            self.hide_time = 0
            self.update_buttons(props)

            debug('on_start_button starting timer')
            OBS2.source_set_text_by_name(self.source_name, self.ticker_text())
            OBS2.timer_add(self.ticker, 1 * 1000)
            OBS2.sceneitem_set_visible_by_name(self.source_name, True)

        return True # Always refresh the GUI.

    #----------------------------------------------------------------------
    def on_stop_button(self, props, prop, settings = None):
        """
        Callback fired from GUI button click.
        """
        self.running = False
        self.stop_time = int(time.time())
        self.hide_time = self.stop_time + 120
        self.update_buttons(props)

        # if not self.running:
        #     debug('stopping any running timer')
        #     OBS2.sceneitem_set_visible_by_name(self.source_name, False)
        #     OBS2.timer_remove(self.ticker)

        return True # Always refresh the GUI.

    #----------------------------------------------------------------------
    def update_buttons(self, props):
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
    p = S.obs_properties_add_list(
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

    # Button to start the timer. (Use the built-in callback, not set_modified_callback)
    start_button = S.obs_properties_add_button(
        props,
        'start_button',
        'Start timer',
        dispatch(e.on_start_button),
    )

    # Button to stop the timer.
    stop_button = S.obs_properties_add_button(
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
    debug(f"script_load importing source_global_name from settings.")
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
    are a few other places. The thing getting updated here is this very
    script, not the settings and not OBS.
    """
    debug(f"script_update starting.")
    e.source_name = S.obs_data_get_string(settings, 'source_prop')

    debug('script_update complete.')

#--------------------------------------------------------------------------
def script_save(settings):
    debug('script_save called.')

#--------------------------------------------------------------------------
def script_unload():
    debug('script_unload called.')


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
# Timers

#--------------------------------------------------------------------------
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
            #S.obs_sceneitem_release(si)
            S.obs_source_release(scene_source)

    #----------------------------------------------------------------------
    @classmethod
    def source_set_text_by_name(self, source_name: str, text: str) -> None:
        source = S.obs_get_source_by_name(source_name)
        if source is not None:
            debug(f"updating text contents to {text}")
            settings = S.obs_data_create()
            S.obs_data_set_string(settings, "text", text)
            S.obs_source_update(source, settings)
            S.obs_source_release(source)
            S.obs_data_release(settings)

###########################################################################
# Global State

e = Events()
