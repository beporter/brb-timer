# BRB Timer for OBS and Twitch Chat

An [OBS Studio](https://obsproject.com/) script written in python to allow [Twitch chatters](https://help.twitch.tv/s/article/chat-basics) in your channel to guess how long a streamer/broadcaster will be AFK.

Exposes some chat commands for mods and chatters to use:

- `!brb` - Mods can unhide and start the count-up timer and enables the `!at MM:SS` command.
- `!at MM:SS` - Chatters register a guess when the streamer will return.
- `!back` - Mods can end an active `!brb` and sends a chat message stating the "winner".

Setup video:

![installation, config and demo of BRB Timer for OBS](https://beporter.github.io/brb-timer/brb-timer_install-config-demo.mp4)


## Installation

- Download the <a href="https://raw.githubusercontent.com/beporter/brb-timer/main/brb_timer.py" download="brb_timer.py">brb_timer.py</a> script from this repo to your computer running OBS. That's the only file required.
- Install Python on your system.
    - Your OBS installation needs to be pointed at an installed version of python between v3.6 and v3.12.
    - Download an installer from python.org for your platform.
        - [Windows 64bit v10+](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
        - [MacOS 64bit universal](https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg)
        - :warning: Pay attention to the install path. We'll need to navigate to it later.
- Launch OBS.
    - From the <kbd>Tools</kbd> menu, choose <kbd>Scripts</kbd>.
    - Select the tab named <kbd>Python Settings</kbd>.
    - Click the <kbd>Browse</kbd> button and locate your python installation folder.
        - On Windows, that path may look like: `C:\Users\YOUR_USERNAME\AppData\Local\Programs\Python\Python312\`
        - On a Mac, possibly: `/opt/homebrew/Cellar/python@3.12/3.12.13_4/Frameworks` (Select the `Frameworks/` folder _inside_ the python installation folder.)
    - When the path is correct, OBS will show a message like "Loaded Python Version: 3.12"
- Install this script into OBS.
    - From the <kbd>Tools > Scripts > Scripts</kbd> tab, click the <kbd>+</kbd> button and navigate to where this file was downloaded.
    - Select this file.
    - OBS should show the "Description" as 'BRB Timer'.
- Quit and relaunch OBS to have the script create the timer Source in the default/active scene.


## Configuration

- The script requires minimal Twitch OAuth access to make API calls and to process chat messages in your channel.
    - Launch OBS.
    - From the <kbd>Tools > Scripts > Scripts</kbd> tab, select the `brb_timer.py` script entry.
    - At the top of the listed properties is a <kbd>Connect Twitch</kbd> button.
    - Click that button to open a [Twitch OAuth window](https://beporter.github.io/brb-timer/start.html) in your default web browser.
    - In your browser, click the "Connect" button to be directed to Twitch, review the requested permissions, and click <kbd>Okay</kbd> if acceptable.
        - You can also review the requested "scopes" in the `OAUTH_SCOPES` constant defined in `brb_timer.py`.
        - Refer to [Twitch's developer reference](https://dev.twitch.tv/docs/authentication/scopes/#twitch-api-and-eventsub-scopes) for what each scope provides.
    - Twitch will redirect your browser back to a landing page with an auth token attached. Copy it from the page.
- Back in OBS, in the BRB Timer properties, paste the Twitch OAuth token.
    - If anything goes wrong, the script log should appear with the relevant log messages.
- The script will have also created a new text Source in your active OBS scene with some default display settings and transitions.
    - You can tweak the text Source properties as much as you like.
    - This script will show/hide and update the count-up timer in this text Source automatically, but it will retain any styling and positioning you customize.
    - :warning: The name of the timer source must remain unchanged in OBS, or this script will try to recreate a new one on next launch.


## Usage

Once you go live and start streaming, three new chat commands will be available:

- `!brb` - **Only accessible to the broadcaster and mods.** Starts the count-up timer, shows it on-stream, and enables the `!at MM:SS` command.
- `!at MM:SS` - Accessible to all chatters, including mods. Registers the user's guess for when the streamer will return. Nobody can guess more than once per !brb, since that'd allow people to cheat by constantly updating their guess.
- `!back` - **Only accessible to the broadcaster and mods.** Ends the `!brb`, hides the on-screen timer (after a cooldown delay), and sends a chat message stating the "winner".
    - The winner is the chat member who registered a guess closest to the count-up timer's final time... without going over-- [Price is Right](https://en.wikipedia.org/wiki/The_Price_Is_Right#One_Bid) rules.


## Assets

- [Twitch Bot Account @brbtimer](https://www.twitch.tv/brbtimer) (Can't really be used due to limitations of twitch user access tokens.)
- [User icon](docs/back-arrow-svgrepo-com.svg) (credit to [SVG Repo](https://www.svgrepo.com/svg/404761/back-arrow))


## References

- https://pytwitchapi.dev/en/stable/index.html
- https://github.com/obsproject/obs-studio/wiki/scripting-tutorial-source-shake
- https://github.com/dmadison/OBS-ChatSpam/blob/master/OBS_ChatSpam.py
- https://github.com/upgradeQ/Streaming-Software-Scripting-Reference#set-current-stream-key
- [Grants description](https://discuss.dev.twitch.com/t/getting-bot-user-id/64363/2)?
    - (bot account `brbtimer` needs to authenticate against the app's client_id with `user:bot user:read:chat user:write:chat` scopes)
    - (end user account `your_username_here` needs to auth against the app's client_id with `channel:bot` scopes)
    - But this only works for a full "app" style token that can be properly secured, which we can't do in an open source python text file.


## License

[MIT](/LICENSE.md)


## Copyright

Copyright &copy; 2026+ Brian Porter
