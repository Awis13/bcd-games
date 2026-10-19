<p align="center">
  <img src="assets/readme/icon.png" width="128" alt="BCD">
</p>

<h1 align="center">BCD</h1>

<p align="center">
  <b>Windows games on iPhone and iPad.</b><br>
  A fast, controller-friendly launcher with Steam and GOG built in.
</p>

<p align="center">
  <a href="https://github.com/thebcd69/bcd-games/releases/latest/download/BCD.ipa"><b>Download the IPA</b></a>
  &nbsp;·&nbsp; <a href="#install">Install</a>
  &nbsp;·&nbsp; <a href="#games">Games</a>
  &nbsp;·&nbsp; <a href="#credits">Credits</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/iOS-18%2B-blue" alt="iOS 18+">
  <img src="https://img.shields.io/badge/license-GPL--3.0-green" alt="GPL-3.0">
  <img src="https://img.shields.io/badge/graphics-Metal-lightgrey" alt="Metal">
</p>

<table>
  <tr>
    <td><img src="assets/readme/library.jpg" alt="Library"></td>
    <td><img src="assets/readme/settings.jpg" alt="Settings"></td>
  </tr>
  <tr>
    <td><img src="assets/readme/accounts.jpg" alt="Accounts"></td>
    <td><img src="assets/readme/controllers.jpg" alt="Controllers"></td>
  </tr>
</table>

BCD runs Windows PC games on iOS and iPadOS. Wine provides Windows, FEX runs x86 and
x64 code on Apple's chips, and DXMT plus BCD's Direct3D 12 runtime draw with Metal. BCD
signs in to Steam, downloads the games you own, and starts them directly or through
Valve's own Steam client (BCD Dock).

BCD is a fork of [Madeira](https://github.com/willfaust/Madeira) by Will Faust and its
contributors (see [Credits](#credits)).

## What it does

- **A whole new app around the games:**
  - your library with store cover art, search, sorting and a menu on every card;
  - a page for each game with all its settings in one place;
  - the controller works everywhere: every screen, the in-game menu, the touch-controls editor;
  - a lightweight in-game menu, performance overlay and on-screen touch controls.
- **Steam:** sign in (password with Steam Guard, or a QR code), your owned library,
  downloads and updates, file checks, Steam Cloud saves. Downloads keep going in the
  background on iOS 26.
- **GOG** (new, early): sign in, download and play your GOG games, with no launcher
  running inside Windows. The Epic Games Store is next.
- **BCD Dock:** Valve's Steam client starts the games that need Steam, without its window.
  BCD never circumvents DRM: those games run through Valve's own client, signed in with your
  account.
- **Graphics:** Direct3D 9, 10 and 11 through DXMT; Direct3D 12 through BCD's runtime and
  Apple's Metal Shader Converter.
- **32-bit and 64-bit games**, controllers (DualSense with rumble, adaptive triggers and light
  bar; Xbox; Switch Pro; MFi), keyboard and mouse, and a controller-to-keyboard-and-mouse mode.
- **Recommended settings that stay current:** every known game ships with tuned settings, and
  one tap on a game's page fetches the newest list. Per-game settings also tune themselves
  after a crash.
- **.NET games** through Wine Mono, downloaded in the app.
- **Feedback from the app:** Settings › Feedback to tell us what is broken or what to improve,
  and Send Log after a crash.

## Games

Tuned settings are built in for these games. They are the licensed Steam versions, started
through BCD Dock:

- God of War
- Ghost of Tsushima
- Horizon Zero Dawn and Horizon Forbidden West
- Red Dead Redemption 2, including the Rockstar Launcher
- GTA V, Legacy and Enhanced
- Crysis Remastered, Crysis 2 Remastered and Crysis 3 Remastered

Also running: Warcraft II: Battle.net Edition. Work in progress: Ratchet & Clank: Rift Apart
(around 20 FPS, with visual glitches).

How well a game runs varies a lot; many games do not start yet.

## What you need

- **iPhone or iPad on iOS / iPadOS 18 or later, with an A13 chip or newer.** Games that use
  geometry shaders or tessellation need an A14 or newer; the heaviest games need an
  A17 Pro or M-series chip and plenty of memory. Devices with an A12 or older chip are not
  supported (their GPUs lack what Direct3D needs).
- **JIT.** iOS lets only an app a debugger starts generate code, and BCD needs that for
  every game:
  - iOS 26 or later on A15 / M2 and newer: BCD's built-in helper (paired once on the
    device), or StikDebug;
  - iOS 18, and iOS 26 on older chips: StikDebug, with its "Always Run Scripts" setting on.
- **A way to install the IPA:** SideStore or AltStore (a free Apple ID works), LiveContainer,
  or your own signing. More memory for big games: GetMoreRam adds Increased Memory Limit
  and extended virtual addressing to BCD's App ID.
- **Room:** each game needs its full install size on the device.

## Install

1. Download [`BCD.ipa`](https://github.com/thebcd69/bcd-games/releases/latest/download/BCD.ipa)
   from the latest [release](https://github.com/thebcd69/bcd-games/releases).
2. Install it with SideStore, AltStore or LiveContainer.
3. Open BCD. Setup walks you through JIT, LocalDevVPN, Steam and the extra runtimes.

## What is not in the package

BCD's packages carry no Microsoft software. The Visual C++ runtime most games need is
downloaded from Microsoft inside the app, after you accept Microsoft's license terms, and
kept unchanged on your device. Wine Mono comes from WineHQ the same way.

## Building

The build is the workflow [.github/workflows/build-ipa.yml](.github/workflows/build-ipa.yml)
and the scripts it runs under `build/` and `tools/`, step by step. Every release also carries
`BCD-source.tar.xz`, the complete source of that release: this repository and all its
submodules at the release's pins.

## License

BCD is free software under the GNU General Public License, version 3 ([LICENSE](LICENSE)),
with the Madeira Converter Exception ([LICENSE-EXCEPTION.md](LICENSE-EXCEPTION.md)).
Madeira's own code is under version 3 or (at your option) any later version; BCD's own
files (marked "BCD Material" in their headers) are under version 3 only, so BCD as a whole
can be used under version 3. They also carry additional terms under GPL-3.0 section 7
([LICENSE-BCD.md](LICENSE-BCD.md)): an app that includes them shows *"Includes work from BCD
by thebcd69 (https://github.com/thebcd69/bcd-games)"* on its About, Credits or Licences
screen, keeps their copyright lines, and marks changed files as modified.
Third-party components and their licences: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
There is no warranty.

Steam is a trademark of Valve Corporation. Windows, DirectX, Direct3D and Visual C++ are
trademarks of the Microsoft group of companies. Apple, iPhone, iPad, iOS, iPadOS and Metal
are trademarks of Apple Inc. BCD is an independent project, not affiliated with or endorsed
by Valve, Microsoft, Apple or the authors of Madeira.

## Credits

**Madeira**, which BCD is a fork of:

- **Will Faust** ([willfaust](https://github.com/willfaust)): created Madeira
- **Nick** ([125hz](https://github.com/125hz)): 32-bit game support, the game library and
  Madeira Dock (BCD Dock here)
- **Jfishin** ([Jfishin](https://github.com/Jfishin)): the original native Steam sign-in,
  library and downloads
- **Jesse** ([JesseLovelace](https://github.com/JesseLovelace)): Steam Cloud saves, faster
  game launches, and fixes that let more games run
- **Dan Perks** ([danperks](https://github.com/danperks)): in-app JIT without StikDebug, and
  pairing without a computer
- **spitefulowl** ([spitefulowl](https://github.com/spitefulowl)): Wine and FEX runtime
  fixes, DXMT texture and memory fixes, audio, the swap tier, and library launch options
- **meshoklv** ([meshoklv](https://github.com/meshoklv)): controller fixes, touch taps that
  stay off the mouse, and a crash-guard fix
- **TheHadesc** ([TheHadesc](https://github.com/TheHadesc)): Madeira Dock starts for more
  games, and a touch gamepad that survives the in-game keyboard

**The projects underneath:** [Wine](https://www.winehq.org), [FEX-Emu](https://github.com/FEX-Emu/FEX),
[DXMT](https://github.com/3Shain/dxmt) by Feifan He (3Shain) with the Direct3D 9 frontend by
David Acevedo (dacevedo12), rpmalloc by Mattias Jansson, StikDebug, StikJIT, idevice,
FreeType, and every library listed in THIRD-PARTY-NOTICES.md. Thank you to everyone who
works on them.

**BCD** by thebcd69.
