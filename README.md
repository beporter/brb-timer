# BRB Timer for OBS and Twitch Chat

An [OBS Studio](https://obsproject.com/) script written in python to allow [Twitch chatters](https://help.twitch.tv/s/article/chat-basics) in your channel to guess how long a streamer/broadcaster will be AFK.

Exposes some chat commands for mods and chatters to use:

- `!brb` - Lets mods start the count-up timer and enables the `!at MM:SS` command.
- `!at MM:SS` - Lets chatters guess when the streamer will return.
- `!back` - Lets mods end an active `!brb` and sends a chat message stating the "winner".


## Installation

- Your OBS installation needs to be pointed at an installed version of python between v3.6 and v3.12.
    - Download an installer from python.org for your platform.
        - [Windows 64bit v10+](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
        - [MacOS 64bit universal](https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg)
    - Pay attention to the install path.
    - Launch OBS.
    - From the `Tools` menu, choose `Scripts`.
    - Select the tab named `Python Settings`.
    - Click the `Browse` button and locate your python installation folder.
    - Select the `Frameworks/` folder _inside_ the python installation folder.
    - When the path is correct, OBS will show a message like "Loaded Python Version: 3.12"
- Install this script into OBS.
    - From the Tools > Scripts > Scripts window, click the `+` button and navigate to where this file was downloaded.
    - Select this file.
    - OBS should show the "Description" as 'BRB Timer for Chat'.


## Configuration

- The script requires minimal Twitch OAuth access to make API calls and to process chat messages in your channel.
    - Launch OBS.
    - From the Tools > Scripts > Scripts window, select the `brb_timer.py` script entry, then click the `Properties` button.
    - At the top of the properties is a `Connect Twitch` button.
    - Click that button to open a Twitch OAuth window in your default web browser.
    - In your browser, review the requests permissions, and click `Okay` if acceptable.
        - You can also review the requested "scopes" in the `TWITCH_OAUTH_SCOPES` constant defined below.
        - Refer to [Twitch's developer reference](https://dev.twitch.tv/docs/authentication/scopes/#twitch-api-and-eventsub-scopes) for what each scope provides.
    - Twitch will redirect your browser window back to this locally running script with an auth token attached.
        - Behind the scenes, this script will use the auth token to request an access token and save it in your OBS settings.
- Back in the BRB Timer properties, most fields should have been auto-populated from the Twitch OAuth.
    - Tweak anything required for your setup.
- The script will have created a new text Source in your active OBS scene with some default display settings and transitions.
    - You can now tweak the text Source properties as much as you like.
    - This script will show/hide and inject the count-up timer into this text Source automatically, but it will retain any styling and positioning you customize.


## Usage

Once you go live and start streaming, three new chat commands will be available:

- `!brb` - Only accessible by mods. Starts the count-up timer, shows it on-stream, and enables the `!at MM:SS` command.
- `!at MM:SS` - By default accessible to all chatters, including mods. Registers the user's guess for when the streamer will return. Nobody can guess more than once per !brb, since that'd allow people to cheat by constantly updating their guess.
- `!back` - Only accessible by mods. Ends the `!brb`, hides the on-screen timer, and sends a chat message stating the "winner" (the chat member who registered a guess closest to the count-up timer's time... without going over-- Price is Right rules.)


## Architecture

Split into a few distinct parts, because OBS doesn't request all of the necessary scopes with its own Twitch OAuth token.

1. Walking the user through obtaining a Twitch API token.
1. Managing/creating a text source to display the count-up timer on screen.
1. Connecting to IRC chat for the correct channel and processing ! commands.


### Twitch OAuth Flow

1. A button in this script's properties is presented in the GUI "Scripts" pane to kick off the Twitch OAuth implicit grant process.
    - In a background thread, a simple HTTP server is started to listen for the eventual OAuth redirect.
    - In the foreground, the user's default web browser is opened to a Twitch OAuth authorization page.
    - When the user confirms, Twitch redirects the user back to the locally running web server.
    - The URL fragment will contain the Twitch auth token (not an API "access" token.)
    - Some Javascript embedded in the redirect landing page extracts the auth token and passes it back to this python script to store in OBS private storage.
1. Once this process completes, this script has the Twitch API access it needs to:
    - determine the broadcaster_id and channel name,
    - connect to chat to listen for messages and post its own,
    - check for moderator privileges
1. The simple http server thread is shut down.
1. This implicit grant type eventually expires. When this script starts up, it checks the expiry date and re-prompts for authorization when necessary.


### Text Source Management (in OBS's) front end

1. When the plugin loads, it checks to see if it's already attached to an existing text Source in the current scene.
    - If OAuth credentials don't already exist, it warns about configuration in its Properties.
    - If a text Source doesn't already exist, it creates one with some defaults.
    - It creates a new "session" for managing ~brb state and any registered guesses.
    - It connects to the channel's IRC chat to listen for commands and send feedback messages.
1. While the stream is running, the chat commands will signal the script to show+start or hide+stop the on-screen count-up timer's text Source.
    - An internal timer is also set to update the source's display text once every second.


### Chat Bot

IRC Thread
    │
    │ enqueue ChatMessage
    ▼
queue.Queue()

OBS timer callback
    │
    │ dequeue
    ▼
CommandParser
    ▼
BRBScript
    ▼
TimerRenderer


## References

https://pytwitchapi.dev/en/stable/index.html
https://github.com/obsproject/obs-studio/wiki/scripting-tutorial-source-shake
https://github.com/dmadison/OBS-ChatSpam/blob/master/OBS_ChatSpam.py
https://github.com/upgradeQ/Streaming-Software-Scripting-Reference#set-current-stream-key


## License

[MIT](/LICENSE.md)


## Copyright

Copyright &copy; 2026 Brian Porter
