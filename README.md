# Project 8 Plus

[website](https://project8.maxleiter.com/) | [docs](https://project8.maxleiter.com/docs)

Project 8 Plus is a PC port of [Tony Hawk's Project 8](https://en.wikipedia.org/wiki/Tony_Hawk%27s_Project_8) (the PS3 version). 

This is not emulation; the P8 Plus launcher builds a PC [recomp](https://recompedia.com/) as a native Windows (or, experimentally, Linux) program. 

P8 Plus provides:
- Support for custom soundtracks, HD textures, and updated smoothed models
- A full Lua modding API and level/script editor
- Uncapped FPS and ultrawide support
- Keyboard and Xbox controller support (with updated in-game button/key prompts)
- Bugfixes for issues encountered in the original game

You provide your own copy of Project 8 and [RPCS3](https://rpcs3.net/) (which is used to decrypt the ROM at setup). No original Tony Hawk's Project 8 assets or code are provided here. Project 8 Plus was made by running Opus 5.5 in a loop for a few weeks.

## Download

**[Download the latest release](https://github.com/MaxLeiter/project-8-plus/releases/latest)** (`Project8Plus-0.9.0-windows-x64.zip`)

You need:
- Your own copy of Tony Hawk's Project 8 for PS3: the US disc (BLUS30011, version 01.02), dumped to a folder
- [RPCS3](https://rpcs3.net/) during setup 
- Windows 10 or 11 (64-bit) and a DirectX 12 graphics card
- About 4 GB of free space, and an internet connection during setup

To install:
1. Unzip the download anywhere and run `THP8 Launcher.cmd`. 
2. Pick your game folder and RPCS3, keep the recommended options, and press **Start building**. The build takes about 20–25 minutes and only happens once.
3. Press **Play**. **Play original** starts the unmodified game without P8 Plus additions.

## HD Textures

<img width="1608" height="450" alt="Original vs HD" src="https://github.com/user-attachments/assets/1820d360-bd9d-4b01-b0f8-ddfa8c23bc2c" />

The majority (~5000) of the game's textures and models are upscaled by an AI model on your own graphics card after install. It takes about an hour on a modern card and you can play while it runs in the background. The launcher downloads the upscaler by itself (44 MB). You can turn HD textures off in the launcher.

<img width="1608" height="450" alt="Original vs HD" src="https://github.com/user-attachments/assets/73035400-4b3e-4be5-b0db-33792884a435" />

## Modding

The launcher's **Mods** page lets you manage your mods. Mods can be written with the Lua scripting API ([docs](https://project8.maxleiter.com/docs)). Working examples are in [`mod/examples`](mod/examples).

### Level Creator

https://github.com/user-attachments/assets/4702b8d0-d43c-4b5c-880a-c62b6b0625f3


The desktop level editor lets you create and script levels with a visual editor. Hotloading is supported when the game is running, so the game will update with each edit in real-time.

## Credits

- [ps3recomp](https://github.com/sp00nznet/ps3recomp) by sp00nznet, the static recompiler this project builds on
- [RPCS3](https://rpcs3.net/), used to decrypt your copy during setup
- HD textures use the 4xTextureDAT2 model by Phhofm ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/))

<sub>License: MIT (LICENSE). Third-party notices: NOTICE (Windows download), NOTICE_linux (Linux download), NOTICE_HD (the HD
pack).
</sub>
