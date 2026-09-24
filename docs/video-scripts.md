# Script


## Installation

- Start on https://github.com/beporter/brb-timer#brb-timer-for-obs-and-twitch-chat

- Right-click the brb_timer.py link, select `Save Link As...`

- Save to Downloads folder.

- Click the Windows py312 download link.

- Click `Save`.

- Launch the installer from the downloads widget.

- Complete the python installer.

- Launch OBS.

- Tools > Scripts.

- Select the `Python Settings` tab.

- Click the `Browse` button.

- Paste this into the address bar: `C:\Users\Demo\AppData\Local\Programs\Python\Python312`

- Click `Select this folder`.

- Click on `Scripts` tab.

- Click the `+` button.

- Select the `Downloads` shortcut from sidebar.

- Select the `brb_timer.py` file and click `Open`.

- Quit OBS.


## Configuration

- Launch OBS.

- Tools > Scripts.

- Select `brb_timer.py`.

- Click the `Connect Twitch` button.

- Confirm opening the URL: https://beporter.github.io/brb-timer/start.html

- In the browser, click the big blue `Connect to Twitch` button.

- Review the requested permissions on the twitch page.

- Click `Okay`.

- On the landing page, select the auth token.

- Right-click, choose `Copy`.

- Switch to OBS Scripts window.

- Right-click in `Twitch OAuth Token` text box, select `Paste`.

- Quit OBS.


## Customization

- Launch OBS.

- Select the `BRB Timer` text source.

- Show and hide the source.

- Move the source to the bottom of the scene.

- Click the `Properties` button.

- Change the font to Arial.

- Change the color to blue.

- Close the properties.

- Right-click the text source, select `Show transition > Properties`.

- Change direction to `Up`.

- Close the properties.

- Right-click the text source, select `Hide transition > Properties`.

- Change direction to `Down`.

- Close the properties.

- Show/hide the source.


## Demo

(prep: Enable the Twitch chat dock inside OBS. Clear the chat history. Turn mic volume all the way down.)

- Launch OBS.

- In the chat dock, type `!brb`.

- In the chat dock, type `!at not a number`.

- In the chat dock, type `!brb`.

- In the chat dock, type `!at 1:05`.

- Wait for the visible text source to pass 1 min and 5 secs.

- In the chat dock, type `!back`.

- Wait for the auto-hide cooldown (120 secs) to hide the source.

- Quit OBS.


## Other Notes


### Image Cropping

- All recordings and screenshots happened in a [UTM](https://mac.getutm.app/) virtual machine running Windows 11, at a resolution of 1024x768.

- All screenshots are of the UTM window itself from MacOS (which gives us nice alpha shadows, but shows the MacOS titlebar and stoplight controls.)

- To remove the window chrome, the images are batch processed using [Acorn](https://flyingmeat.com/acorn/) and [Automator](https://support.apple.com/guide/automator/welcome/mac).

- Outer screenshot dimension: `1836px` wide x `2272px` tall (at 144 DPI).

- Position of MacOS window chrome: `0px` right x `1684px` up.

- Size of the crop: `76px` tall x `2272px` across.

