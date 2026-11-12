<img src="oit.png" width="96" align="right" alt="OIT">

# Obliphur's Integrated Terminal (OIT)

AI model and voice configurations fetch model lists automatically; MiniMax speech models come from its official documentation. Voice creation supports text descriptions and recording uploads for cloning, with previews before applying a voice. Play message audio with the icon at the bottom-left edge of its bubble. Manual playback, automatic playback and voice previews are available only while idle or on a break, and stop when that state ends.

> (Not actually an integrated terminal...)

[中文](README.md) | English

Have you ever felt that the time you spend online is empty, yet afterwards you keep drifting with the tide of the internet without being able to stop?
Have you ever found yourself distracted by one site after another, feeling guilty and miserable about how inefficient you are?
Have you ever wanted to change your lifestyle, focus on things that are not online, and improve yourself?

This project exists for exactly those questions. You can configure the relevant settings on first run according to your habits.
It tries to be considerate of new users who are not used to this mode, so it includes a few features for that.

Would you entrust the rest of your life to me?


By the way, praise Claudia...

***

## ⚠️ Warning

- This project **contains AI-generated code**, and this document was organized by AI as well.
- Because of the special and closed nature of iOS, it will never support iOS.
- It includes: auto-start on boot, cross-device launch, blocking VPN changes, and other forceful measures.
However, all data transfers in this project happen on the local network and are never uploaded to any server.

---

## Features

| Page | What it does |
| --- | --- |
| **Timer** | Focus timer; suggests how long to rest when it ends |
| **Network** | Request-based network access: connects automatically after a cooling-off period, cuts off at the deadline; also quick access and emergency loans; the "📁 File Channel" transfers files / images between a bound phone and computer |
| **Rules** | Website / app blacklist and whitelist in Proxy mode (a blocked program's window is closed as soon as it comes to the foreground); AI can suggest entries; a bilibili.com entry can be expanded to allow / block by uploader |
| **Chat** | Chat with a persona AI with schedule-based status and proactive messages; optional MiniMax voice profiles per persona, cached audio, manual playback or automatic playback of recent unheard messages; setup help for model and voice configurations |
| **Hour Log** | Prompts you every hour to record what you did in the previous hour |
| **Calendar** | Study time by day |
| **Statistics** | Distribution of activities, network reasons, and foreground time per program; recent completed contracts and recent violations |
| **Contract** | Contracts: time-limited challenges where you pick terms and add difficulty |
| **Settings** | Adjustable parameters, grouped into pages |
| **Reset** | Restore networking and clear data |

There are two ways to cut off the network: **Adapter** disables the network adapter directly; **Proxy** filters through the system proxy, and programs that do not read the system proxy are unaffected.

---

## Installation

Download the latest **`setup.exe`** from [Releases](https://github.com/Obliphur/Obliphur-s-Integrated-Terminal/releases) on GitHub and run it:
it checks and installs dependencies (downloading the official Python from python.org if Python 3.10+ is missing), installs OIT to `C:\Program Files\OIT` (changeable),
and registers it as software in the system (Settings → Apps → Installed apps), with optional desktop / Start menu shortcuts. Silent install: `setup.exe /S`. See `MECHANICS.md` 16.4 for details.
For the phone version, download `OIT.apk` from the same release.

## Running

- Windows 10 / 11, Python 3.10+, standard library only (pywin32 optional)
- Requests administrator privileges on startup (needed to cut off the network)
- Without setup.exe, download the source and run it directly:

```bash
python main.py
```

## Updates

Each time the program opens it checks for updates from the latest GitHub release. Auto-check / auto-update can be turned off in Settings → Basic Settings. The rules are in `MECHANICS.md` section 16.

## Android version

`Android version/` is the Android version (Kotlin + Jetpack Compose, Android 10 and above); it cuts off the network with a local VPN. See its `README.md` for the build steps, and `MECHANICS.md` section 14 for the differences from the desktop version.

## Development

Each module can run its own self-test, for example `python -m net_guard`, `python settings_manager.py`...

### Project structure

```text
obliphur_terminal/
├─ main.py               Entry point: request admin rights, start the UI
├─ app/                Main window: pages, timer, network page, scheduled checks, close / reset
├─ config.py             Defaults, themes and font sizes, version (APP_VERSION)
├─ themes/               UI themes (classic / claude / urban_will; the urban_will font and preview live here)
├─ settings_manager.py   Setting definitions, 48-hour cooling-off period, configuration period
├─ data_manager/         Study time, hour logs, focus periods, window usage time (statistics; mixins in data_manager/)
├─ daily.py              Sleep time (Settings → Daily)
├─ behavior.py           Behavior modes / going out (optional; adds to statistics and hour logs)
├─ prescript/            Prescripts: draw / timer / save, exclusive to the urban_will theme (mixins in prescript/)
├─ comm/                 Chat: personas, chat history, proactive messages, the other party's status
│  ├─ comm_data.py          Chat: data sent with 📎 / reference templates the other party can query
├─ alarm.py              Alarm: the device alarm, ringtones, and when the co-time alarm (follows the other party's wake-up time) rings
├─ achievements.py       Achievements: condition checks and saving
├─ contract/             Contracts: terms, violations and demotion, rest multiplier, hour-log locking (mixins in contract/)
├─ net_guard/            Network quota, requests / quick access / loans, switching cut-off method (mixins in net_guard/)
├─ netproxy/             Local filtering proxy (Proxy cut-off method)
│  ├─ server.py          HTTP / HTTPS tunnel proxy, 403 block page
│  ├─ rules.py           Blacklist / whitelist and decisions, cooling-off period for list changes
│  ├─ controller.py      Ties the proxy, the lists and the system proxy together
│  ├─ sysproxy.py        Install / restore the Windows system proxy
│  ├─ procinfo.py        Connection → the program that started it
│  ├─ upgate.py          Allow / block by uploader: page → uploader (public Bilibili API)
│  ├─ browserurl.py      Read the browser address bar (UI Automation)
│  └─ selftest.py        End-to-end self-test
├─ window_guard.py       Taskbar window limit (reminder while focusing), close programs by foreground window, foreground program detection, hide / show windows
├─ prestart.py           Pre-start (PC only): hide / mute, and keep them going when focus restarts directly from a break
├─ audiomute.py          Mute / unmute by process (audio sessions, used by pre-start)
├─ recorder/             Contract recording (camera / screen, using Windows' built-in H.264 encoder; storage / MF / merging in recorder/)
├─ devsync/              Device sync: discovery, pairing, encryption, syncing, direct package / file transfer (mixins / utils in devsync/)
├─ sync_local.py         Device sync ↔ local data
├─ firewall.py           Windows Firewall: allow the sync port
├─ updater.py            Check / install updates from GitHub
├─ ai_assist/            AI list suggestions: providers, prompts, parsing (config / HTTP / search in ai_assist/)
├─ context_collect.py    Local information for AI suggestions (installed apps, domains in browsing history)
├─ i18n/                 Chinese / English UI strings
├─ toast.py              Notifications / confirmations / notices that can only be dismissed by hand
├─ shortcut.py           Desktop shortcut, Start menu
├─ uninstall.py / .bat   Standalone uninstaller
├─ pages/                UI for each page
│  ├─ settings_page/     Settings (grouped into pages; forms / cards / chat / recording in settings_page/)
│  ├─ sync_panel.py      Device sync (Settings → Basic Settings)
│  ├─ rules_page.py      Rules (lists)
│  ├─ up_dialog.py       Uploader lists under a website entry
│  ├─ file_channel.py    File Channel: send files / images to bound devices
│  ├─ ai_dialogs.py      AI suggestion dialogs
│  ├─ stats_page.py      Statistics
│  ├─ calendar_page.py   Calendar
│  ├─ comm_page.py       Chat (persona conversations)
│  ├─ hour_log_page.py   Hour log
│  ├─ outing_dialog.py   Going-out duration window
│  ├─ achievements_page.py  Achievements page (Settings → Basic Settings → 🏆 Achievements), unlock notices
│  ├─ contract_page.py   Contract page
│  ├─ prescript_page.py  Prescript page (urban_will theme only)
│  ├─ prestart_dialog.py Pre-start program list dialog
│  └─ widgets.py         Shared widgets (scroll area, switches, loading page ...)
├─ defaults/             Empty templates (lists, AI config)
├─ sounds/               Contract violation alarms
├─ MECHANICS.md          Exact rules and numbers for every mechanism
├─ for_developer.md      Development conventions
└─ Android version/      Android version
   └─ app/src/main/java/com/obliphur/oit/
      ├─ OitApp.kt       App entry, platform-specific implementations, updates / sending updates to other devices
      ├─ core/           Platform-independent logic (mirrors the desktop modules)
      │  ├─ Core.kt      Everything wired together: timers, VPN schedule, app locking
      │  ├─ Settings.kt  Settings and cooling-off periods
      │  ├─ NetGuard.kt  Network quota and requests
      │  ├─ Timer.kt     Focus timer
      │  ├─ Daily.kt     Sleep time
      │  ├─ Behavior.kt  Behavior modes / going out
      │  ├─ Rules.kt     Blacklist / whitelist
      │  ├─ DataStore.kt Statistics data
      │  ├─ Achievements.kt Achievements
      │  ├─ Contract.kt  Contracts
      │  ├─ Config.kt    Numbers, utility functions
      │  ├─ Store.kt     File read / write
      │  └─ I18n.kt / EnStrings.kt  English UI
      ├─ service/        Foreground service (Platform.kt), VPN (OitVpnService.kt, Packets.kt),
      │                  app locking (AppLock.kt), updates (Updater.kt)
      ├─ sync/           Device sync (SyncService.kt, SyncCrypto.kt, AndroidLocal.kt)
      ├─ ai/Ai.kt        AI list suggestions
      └─ ui/             Pages (Jetpack Compose)
```
