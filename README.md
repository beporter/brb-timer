# BRB Timer for OBS and Twitch Chat

An [OBS Studio](https://obsproject.com/) script written in python to allow [Twitch chatters](https://help.twitch.tv/s/article/chat-basics) in your channel to guess how long the streamer/broadcaster will be AFK.

Exposes some chat commands for mods and chatters to use:

- `!brb` - Mods can unhide and start the count-up timer and enables the `!at MM:SS` command.
- `!at MM:SS` - Chatters register a guess when the streamer will return.
- `!back` - Mods can end an active `!brb` and sends a chat message stating the "winner".

Demo video:

<video src="https://beporter.github.io/brb-timer/BRBTimer_Demo.mp4" alt="BRB Timer for OBS demo video">


## Installation

Setup video:

<video src="https://beporter.github.io/brb-timer/BRBTimer_install_and_config.mp4" alt="installing and configuring BRB Timer for OBS">

1. Download the <a download href="https://raw.githubusercontent.com/beporter/brb-timer/main/brb_timer.py">brb_timer.py</a> script to your computer running OBS. That's the only file required.
    ![download brb_timer.py from GitHub](docs/download-from-github.png)
1. Install Python on your system. (OBS requires a python version between v3.6 and v3.12.)
    - :warning: Pay attention to the install path. We'll need to navigate to it later.
    - [Windows v10+ 64bit](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
        ![run the python installer from downloads](docs/run-python-installer-windows.png)
        ![python installation path](docs/python-install-path-windows.png)
    - [MacOS universal](https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg)
1. Launch OBS.
    - From the <kbd>Tools</kbd> menu, choose <kbd>Scripts</kbd>.
        ![OBS > Tools > Scripts menu](docs/obs-tools-scripts-menu.png)
    - Select the tab named <kbd>Python Settings</kbd>.
        ![OBS Python Settings pane](docs/obs-scripts-python-pane.png)
    - Click the <kbd>Browse</kbd> button and locate your python installation folder.
        - On Windows, that path may look like: `C:\Users\YOUR_USERNAME\AppData\Local\Programs\Python\Python312\`
            ![Windows file explorer window navigated to python install path](docs/obs-find-python-install-path.png)
        - On a Mac, `/Library/Frameworks/Python.framework` or possibly: `/opt/homebrew/Cellar/python@3.12/3.12.13_4/Frameworks` (Be sure to select the `Frameworks/` folder _inside_ the python installation folder.)
            ![MacOS Finder window navigated to python install path]()
    - When the path is correct, OBS will show a message like "Loaded Python Version: 3.12"
        ![OBS showing recognized python version](docs/obs-recognized-python.png)
1. Install this script into OBS.
    - From the <kbd>Tools > Scripts > Scripts</kbd> tab, click the <kbd>+</kbd> button, navigate to where the `brb_timer.py` file was downloaded, and select it.
        ![File explorer navigated to downloaded brb_timer.py file](docs/obs-add-brb_timer.png)
    - OBS should show the _Description_ as 'BRB Timer'.
        ![BRB Timer script properties in OBS](docs/obs-brbtimer-properties.png)
1. Quit and relaunch OBS to have the script create the timer Source in the default/active scene.


## Configuration

The script requires minimal Twitch OAuth access to make API calls and to process chat messages in your channel.

1. Visit [this page](https://beporter.github.io/brb-timer/start.html) and click the "Connect to Twitch" button to obtain an auth token from Twitch. Copy the token to your clipboard.
    ![Twitch OAuth page](docs/twitch-auth-page.png)
    ![Landing page with token to copy](docs/copy-twitch-auth-token.png)
1. Launch OBS.
    - From the <kbd>Tools > Scripts > Scripts</kbd> tab, select the `brb_timer.py` script entry.
        ![OBS Tools > Scripts menu](docs/obs-tools-scripts-menu.png)
    - In the BRB Timer properties, paste the Twitch OAuth token.
        ![BRB Timer properties in OBS Scripts pane](docs/paste-twitch-token-in-obs.png)
    - If anything goes wrong, the script log should appear with the relevant log messages.
        ![OBS Scripts log](docs/obs-scripts-log.png)


## Customization

<video src="https://beporter.github.io/brb-timer/BRBTimer_Customize.mp4" alt="customizing the on-screen BRB Timer">

The script will have also created a new text Source in your active OBS scene with some default display settings and transitions.

![BRB Timer text source in OBS](docs/obs-text-source.png)

This script will show/hide and update the count-up timer in this text Source automatically, but it will retain any styling and positioning you customize, so you can tweak the text Source properties as much as you like.

![Source properties](docs/obs-tools-scripts-menu.png)

:warning: The **name** of the timer source must remain unchanged in OBS, or this script will try to recreate a new one on next launch.


## Usage

Once you go live and start streaming, three new chat commands will be available:

- `!brb` - **Only accessible to the broadcaster and mods.** Starts the count-up timer, shows it on-stream, and enables the `!at MM:SS` command.
- `!at MM:SS` - Accessible to all chatters, including mods. Registers the user's guess for when the streamer will return. Nobody can guess more than once per !brb, since that'd allow people to cheat by constantly updating their guess.
- `!back` - **Only accessible to the broadcaster and mods.** Ends the `!brb`, hides the on-screen timer (after a cooldown delay), and sends a chat message stating the "winner".
    - The winner is the chat member who registered a guess closest to the count-up timer's final time... without going over-- [Price is Right](https://en.wikipedia.org/wiki/The_Price_Is_Right#One_Bid) rules.

![BRB Timer in action, in a Twitch chat channel](docs/brbtimer-chat-demo.png)


## License

[MIT](/LICENSE.md)


## Copyright

Copyright &copy; 2026+ Brian Porter
