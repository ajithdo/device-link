# Device Link

Send links from your Android phone to your computer and keep the clipboard in sync both ways, using Telegram as the relay. You don't need a server or to be on the same network.

```
 Android app ──(phone bot)──►  private Telegram channel  ◄──(laptop bot)── desktop/device_link.py
   share "Send to laptop"         #link / #clip posts           opens links in the browser
   clipboard ⇄                                                    clipboard ⇄
```

Why two bots? A Telegram bot never receives its own messages. Each device gets its own bot, and both bots are admins of one private channel, so each side sees what the other posts.

| Feature | How |
|---|---|
| Share link → laptop browser | Pick **Send to laptop** in Android's share menu |
| Laptop clipboard → phone | Automatic. The desktop script checks the clipboard every 0.5 s; the phone receives through a Telegram long-poll |
| Phone clipboard → laptop | Automatic after a one-time ADB grant (Android 10+), or use the notification button / Quick Settings tile / share text |
| Links posted by hand in the channel | Also opened on the laptop |
| Plain text posted by hand in the channel | Copied to the laptop clipboard (`"plain_text_to_clipboard": false` to turn off) |
| Long text (>4000 chars) | Sent as a `clipboard.txt` document, which the other side downloads |

## 1. Telegram setup (about 5 min)

1. In Telegram, open **@BotFather** → `/newbot` twice. Create one bot for the phone (e.g. `myname_phone_link_bot`) and one for the laptop (e.g. `myname_laptop_link_bot`). Keep both tokens.
2. Create a **new private channel** (e.g. "Device Link").
3. Channel → Administrators → **Add admin**, and add **both bots**. Each needs the *Post messages* permission.

## 2. Desktop (Windows / macOS / Linux, Python 3.9+)

```bash
cd desktop
pip install -r requirements.txt
python device_link.py --setup     # paste the LAPTOP bot token, then post "hi" in the channel
python device_link.py             # run it
```

`--setup` finds the channel ID and saves `config.json`. Write that ID down, because the phone app needs it too.

Options: `--no-clipboard`, `--no-links`, `--skip-backlog` (don't open links shared while the script was off), `--diagnose`, `-v`.
You can also use the environment variables `DEVICE_LINK_BOT_TOKEN` and `DEVICE_LINK_CHAT_ID` instead of `config.json`.

**Linux:** pyperclip needs a clipboard tool: `sudo apt install xclip` (X11) or `wl-clipboard` (Wayland).

**Run at login**
- Windows: create a shortcut to `pythonw device_link.py` in `shell:startup`.
- macOS / Linux: add `python3 /path/to/device_link.py` to Login Items / your desktop's autostart (or a `systemd --user` service).

### Troubleshooting the desktop script

Every post the script receives is logged with what it did with it, for example:

```
INFO Received clip post from phone: 'copied on phone'
INFO   -> copied to clipboard (15 chars)
```

If nothing shows up when you post in the channel, run:

```bash
python device_link.py --diagnose
```

It checks the token, whether a webhook is blocking polling, whether the bot can see the channel and is an admin, and whether the clipboard works. Then it prints every post the bot receives for 90 seconds.

| Symptom | Cause / fix |
|---|---|
| `Ignoring post from channel …; config chat_id is …` | The channel ID in `config.json` is wrong. Run `--setup` again |
| `Conflict: terminated by other getUpdates request` | The same bot token is used on the phone, or the script is running twice. Each device needs its **own** bot |
| Your own posts show up but `from=phone` ones never do | The phone app uses the wrong token or channel ID |
| Script stops printing and syncing (Windows) | You clicked in the console and it entered "Select" mode. Press **Esc**. The script now turns this mode off when it starts |
| `could not write to the clipboard` | Another app is holding the clipboard. The script retries 5 times before giving up |

## 3. Android app (Android 8.0+)

**Install:** download `device-link-debug-apk` from the latest GitHub Actions run (Actions → Build), or build it yourself:

```bash
cd android
./gradlew assembleDebug          # → app/build/outputs/apk/debug/app-debug.apk
```

Then sideload it (allow "Install unknown apps").

**Configure:**
1. Open **Device Link** and paste the **phone** bot token.
2. Tap **Detect** and post anything in the channel, or type the channel ID from the desktop setup.
3. Tap **Save & test**. A ✅ message should appear in the channel.
4. Tap **Start sync**. A persistent notification appears.
5. Tap **Disable battery optimization** so Android doesn't kill the sync in the background.

### Automatic phone → laptop clipboard (Android 10+)

Android 10 and newer only lets the app in front read the clipboard. To sync phone copies automatically:

1. Enable *Developer options → USB debugging*, connect to the computer, and run once:
   ```bash
   adb shell pm grant com.devicelink android.permission.READ_LOGS
   ```
2. In the app, tap **Allow display over other apps** and enable it.
3. Stop and start sync again.

How it works: when you copy something, Android logs that it denied the app clipboard access. The app sees that log line and briefly adds an invisible 1×1 window so it can read the clipboard, then removes it. On Android 13+ a system dialog *"Allow Device Link to access all device logs?"* may appear when sync starts; choose **Allow**. Android 12+ also shows a small "Device Link pasted from your clipboard" toast. That's expected.

**Without ADB** you can still send the phone clipboard yourself:
- the **Send clipboard** button on the sync notification,
- the **Send clipboard** Quick Settings tile (edit your quick settings panel to add it),
- share any text to **Send to laptop**. Text without a link goes to the laptop's clipboard.

Laptop → phone clipboard sync is always automatic. On Android < 10, both directions are automatic with no extra steps.

## Message format

Posts in the channel are plain text, so you can read the history in Telegram:

```
#link #from_phone          #clip #from_laptop
https://example.com        text that was copied
```

Each device ignores posts tagged with its own name. A post without tags that contains URLs (for example, one you type yourself) is opened by the desktop.

## Security notes

- Anyone who can post in the channel can make your laptop open URLs, so keep the channel **private** and its admins limited to you and the two bots. The desktop only acts on posts from the configured channel ID and only opens `http(s)` links.
- Clipboard contents (including any passwords you copy) pass through Telegram's servers. Pause sync (`--no-clipboard`, or **Stop** in the app) if that matters to you.
- Bot tokens are secrets. `desktop/config.json` is git-ignored.

## Tests

```bash
cd desktop && python -m unittest -v
cd android && ./gradlew testDebugUnitTest
```
