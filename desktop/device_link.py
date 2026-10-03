#!/usr/bin/env python3
"""Device Link - desktop side.

Polls a Telegram bot for posts in a private channel shared with the phone app:
  * "#link" posts (or any plain post containing URLs) are opened in the default browser.
  * "#clip" posts are written to the local clipboard.
  * Local clipboard changes are posted to the channel as "#clip" so the phone picks them up.

Both devices use their *own* bot (Telegram bots never receive their own messages),
and both bots are administrators of the same private channel.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: pip install -r requirements.txt")

log = logging.getLogger("device-link")

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4000          # Telegram hard limit is 4096; keep headroom for the header line
MAX_CLIP_BYTES = 5 * 1024 * 1024
DEFAULT_CONFIG = Path(__file__).with_name("config.json")

URL_RE = re.compile(r"""https?://[^\s<>"'`]+""", re.IGNORECASE)
TRAILING_PUNCT = ".,;:!?)]}'\""
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


# --------------------------------------------------------------------------- protocol

KIND_LINK = "link"
KIND_CLIP = "clip"


@dataclass
class Message:
    kind: Optional[str]          # "link", "clip" or None for a plain post
    source: Optional[str]        # device name from "#from_<name>", if any
    payload: str


def sanitize_device(name: str) -> str:
    """Hashtags only allow letters, digits and underscores."""
    cleaned = re.sub(r"\W", "_", name.strip().lower()) or "device"
    return cleaned[:32]


def build_header(kind: str, device: str) -> str:
    return f"#{kind} #from_{sanitize_device(device)}"


def build_message(kind: str, device: str, payload: str) -> str:
    return f"{build_header(kind, device)}\n{payload}"


def parse_message(text: str) -> Message:
    """Parse a channel post. The first line may carry "#link"/"#clip" and "#from_x" tags."""
    first, _, rest = text.partition("\n")
    tags = first.split()
    if tags and all(t.startswith("#") for t in tags):
        kind = None
        source = None
        for tag in tags:
            low = tag.lower()
            if low in ("#link", "#clip"):
                kind = low[1:]
            elif low.startswith("#from_"):
                source = low[len("#from_"):]
        if kind:
            return Message(kind, source, rest)
    return Message(None, None, text)


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(TRAILING_PUNCT)
        if url and url not in urls:
            urls.append(url)
    return urls


# --------------------------------------------------------------------------- telegram


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, session: Optional[requests.Session] = None):
        self.token = token
        self.session = session or requests.Session()

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def call(self, method: str, http_timeout: float = 30, files=None, **params):
        while True:
            if files:
                resp = self.session.post(self._url(method), data=params, files=files, timeout=http_timeout)
            else:
                resp = self.session.post(self._url(method), json=params, timeout=http_timeout)
            try:
                data = resp.json()
            except ValueError:
                raise TelegramError(f"{method}: HTTP {resp.status_code}") from None
            if data.get("ok"):
                return data["result"]
            retry = (data.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry:
                log.warning("Rate limited by Telegram, waiting %ss", retry)
                time.sleep(int(retry) + 1)
                continue
            raise TelegramError(f"{method}: {data.get('description', resp.status_code)}")

    def get_me(self):
        return self.call("getMe", http_timeout=15)

    def get_updates(self, offset: Optional[int], timeout: int = 25):
        params = {"timeout": timeout, "allowed_updates": ["channel_post"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", http_timeout=timeout + 10, **params)

    def get_webhook_info(self):
        return self.call("getWebhookInfo", http_timeout=15)

    def get_chat(self, chat_id):
        return self.call("getChat", http_timeout=15, chat_id=chat_id)

    def get_chat_member(self, chat_id, user_id):
        return self.call("getChatMember", http_timeout=15, chat_id=chat_id, user_id=user_id)

    def send_text(self, chat_id, text: str):
        return self.call("sendMessage", http_timeout=20, chat_id=chat_id, text=text,
                         disable_web_page_preview=True, disable_notification=True)

    def send_document(self, chat_id, filename: str, content: bytes, caption: str):
        files = {"document": (filename, io.BytesIO(content), "text/plain")}
        return self.call("sendDocument", http_timeout=60, files=files, chat_id=chat_id,
                         caption=caption, disable_notification="true")

    def download_file(self, file_id: str) -> bytes:
        info = self.call("getFile", http_timeout=20, file_id=file_id)
        resp = self.session.get(f"{API_BASE}/file/bot{self.token}/{info['file_path']}", timeout=60)
        resp.raise_for_status()
        return resp.content


def send_clip(tg: Telegram, chat_id, device: str, text: str):
    """Short text goes as a message, long text as a .txt document."""
    if len(text) <= MAX_MESSAGE_LEN:
        tg.send_text(chat_id, build_message(KIND_CLIP, device, text))
    else:
        tg.send_document(chat_id, "clipboard.txt", text.encode("utf-8"),
                         build_header(KIND_CLIP, device))


def summarize(text: str, post: Optional[dict] = None, limit: int = 60) -> str:
    if post and post.get("document"):
        return f"[file {post['document'].get('file_name', '')}]"
    one_line = " ".join(text.split())
    return repr(one_line if len(one_line) <= limit else one_line[:limit] + "...")


def same_text(a: Optional[str], b: Optional[str]) -> bool:
    """Compare clipboard text ignoring CRLF/LF differences (Windows clipboard uses CRLF)."""
    if a is None or b is None:
        return a is b
    return a.replace("\r\n", "\n") == b.replace("\r\n", "\n")


def polling_hint(exc: Exception) -> str:
    text = str(exc)
    if "Conflict" in text and "webhook" in text:
        return " -> this bot has a webhook set; run --diagnose to remove it."
    if "Conflict" in text:
        return (" -> another program is polling this bot. Make sure the phone app uses the OTHER bot's token"
                " and only one copy of this script is running.")
    if "Unauthorized" in text:
        return " -> the bot token is wrong or was revoked; run --setup again."
    return ""


def disable_windows_quickedit():
    """Clicking in a Windows console enables QuickEdit selection, which freezes this script.

    Turn QuickEdit off for this console so a stray click can't stall syncing."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            kernel32.SetConsoleMode(handle, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
    except Exception:  # noqa: BLE001 - purely a convenience
        pass


# --------------------------------------------------------------------------- clipboard


class Clipboard:
    """Thin wrapper around pyperclip that degrades gracefully when unavailable."""

    def __init__(self):
        try:
            import pyperclip
            pyperclip.paste()
            self._pc = pyperclip
        except Exception as exc:  # noqa: BLE001 - pyperclip raises various types
            log.error("Clipboard unavailable (%s). On Linux install xclip, xsel or wl-clipboard.", exc)
            self._pc = None

    @property
    def available(self) -> bool:
        return self._pc is not None

    def paste(self) -> Optional[str]:
        try:
            return self._pc.paste()
        except Exception as exc:  # noqa: BLE001
            log.debug("Clipboard read failed: %s", exc)
            return None

    def copy(self, text: str, attempts: int = 5):
        """Copy with retries: on Windows another app may briefly hold the clipboard open."""
        for attempt in range(1, attempts + 1):
            try:
                self._pc.copy(text)
                return
            except Exception as exc:  # noqa: BLE001 - pyperclip raises various types
                if attempt == attempts:
                    raise
                log.debug("Clipboard write failed (%s), retrying", exc)
                time.sleep(0.2 * attempt)


# --------------------------------------------------------------------------- app


@dataclass
class Config:
    bot_token: str
    chat_id: int
    device_name: str = "laptop"
    open_links: bool = True
    sync_clipboard: bool = True
    clipboard_poll_seconds: float = 0.5
    open_backlog_links: bool = True
    plain_text_to_clipboard: bool = True   # untagged posts without links -> clipboard

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        token = os.environ.get("DEVICE_LINK_BOT_TOKEN", data.get("bot_token", ""))
        chat = os.environ.get("DEVICE_LINK_CHAT_ID", data.get("chat_id", ""))
        if not token or not chat:
            raise SystemExit(f"bot_token and chat_id are required. Run: python {Path(__file__).name} --setup")
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        known.update(bot_token=token, chat_id=int(chat))
        return cls(**known)


@dataclass
class LinkSync:
    tg: Telegram
    cfg: Config
    clipboard: Optional[Clipboard] = None
    open_url: callable = webbrowser.open
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_clip: Optional[str] = None     # last value we sent or received; never echoed back
    _stop: threading.Event = field(default_factory=threading.Event)

    @property
    def me(self) -> str:
        return sanitize_device(self.cfg.device_name)

    # ---- incoming

    def handle_updates(self, updates: Iterable[dict], backlog: bool = False):
        """Process a batch of updates. Only the newest clip in a batch is applied.

        - "#clip" posts           -> clipboard
        - "#link" posts           -> opened in the browser
        - untagged posts with URLs -> opened in the browser
        - untagged plain text      -> clipboard (plain_text_to_clipboard)
        """
        latest_clip = None
        for upd in updates:
            post = upd.get("channel_post")
            if not post:
                log.debug("Skipping non-channel update %s", upd.get("update_id"))
                continue
            chat = post.get("chat", {})
            if chat.get("id") != self.cfg.chat_id:
                log.warning("Ignoring post from channel %s (%r); config chat_id is %s. "
                            "Re-run --setup if this is your channel.",
                            chat.get("id"), chat.get("title"), self.cfg.chat_id)
                continue
            text = post.get("text") or post.get("caption") or ""
            msg = parse_message(text)
            log.info("Received %s post from %s: %s", msg.kind or "plain", msg.source or "channel",
                     summarize(msg.payload, post))
            if msg.source == self.me:
                log.info("  -> skipped: tagged with this device's own name '%s'", self.me)
                continue
            if msg.kind == KIND_CLIP:
                latest_clip = (post, msg)
                continue
            urls = extract_urls(msg.payload)
            if msg.kind is None and not urls:
                if not self.cfg.plain_text_to_clipboard:
                    log.info("  -> skipped: plain text (plain_text_to_clipboard is off)")
                elif not msg.payload.strip() and not post.get("document"):
                    log.info("  -> skipped: no text (photos/stickers are not synced)")
                else:
                    latest_clip = (post, msg)
                continue
            if backlog and not self.cfg.open_backlog_links:
                log.info("  -> skipped: link was shared while the script was off (--skip-backlog)")
                continue
            self._open_links(urls)
        if latest_clip:
            self._apply_clip(*latest_clip)

    def _open_links(self, urls: list[str]):
        if not self.cfg.open_links:
            log.info("  -> skipped: link opening is disabled (--no-links)")
            return
        for url in urls:
            log.info("  -> opening %s", url)
            self.open_url(url)

    def _apply_clip(self, post: dict, msg: Message):
        if not (self.cfg.sync_clipboard and self.clipboard and self.clipboard.available):
            log.info("  -> skipped: clipboard sync is off or the clipboard is unavailable")
            return
        text = msg.payload
        doc = post.get("document")
        if doc:
            if doc.get("file_size", 0) > MAX_CLIP_BYTES:
                log.warning("Ignoring oversized clipboard document")
                return
            text = self.tg.download_file(doc["file_id"]).decode("utf-8", errors="replace")
        try:
            with self._lock:
                self.clipboard.copy(text)
                self._last_clip = text
        except Exception as exc:  # noqa: BLE001
            log.error("  -> could not write to the clipboard: %s", exc)
            return
        log.info("  -> copied to clipboard (%d chars)", len(text))

    # ---- outgoing

    def check_clipboard(self):
        """Send the local clipboard if it changed since the last sync. Returns True if sent."""
        current = self.clipboard.paste()
        if current is None or not current.strip():
            return False
        with self._lock:
            if same_text(current, self._last_clip):
                return False
            self._last_clip = current
        if len(current.encode("utf-8")) > MAX_CLIP_BYTES:
            log.warning("Clipboard too large to sync, skipping")
            return False
        try:
            send_clip(self.tg, self.cfg.chat_id, self.cfg.device_name, current)
            log.info("Sent clipboard (%d chars)", len(current))
        except (TelegramError, requests.RequestException) as exc:
            log.error("Failed to send clipboard: %s", exc)
            with self._lock:
                self._last_clip = None   # retry on next tick
            return False
        return True

    def clipboard_loop(self):
        self._last_clip = self.clipboard.paste()   # don't push whatever was there at startup
        while not self._stop.wait(self.cfg.clipboard_poll_seconds):
            self.check_clipboard()

    # ---- main loop

    def run(self):
        me = self.tg.get_me()
        log.info("Connected as @%s, device '%s', channel %s", me["username"], self.me, self.cfg.chat_id)
        if self.cfg.sync_clipboard and self.clipboard and self.clipboard.available:
            threading.Thread(target=self.clipboard_loop, daemon=True, name="clipboard").start()

        disable_windows_quickedit()
        log.info("Waiting for channel posts... (run with --diagnose if nothing arrives)")
        offset = None
        first = True
        backoff = 1
        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(offset)
                backoff = 1
            except (TelegramError, requests.RequestException) as exc:
                log.warning("Polling failed: %s (retrying in %ss)%s", exc, backoff, polling_hint(exc))
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if updates:
                offset = updates[-1]["update_id"] + 1
                try:
                    self.handle_updates(updates, backlog=first)
                except Exception:  # noqa: BLE001 - never let one bad post kill the loop
                    log.exception("Failed to handle updates")
            first = False

    def stop(self):
        self._stop.set()


# --------------------------------------------------------------------------- setup wizard


def setup(path: Path):
    print("Device Link setup\n")
    print("1. In @BotFather create a bot for this computer (e.g. 'my_laptop_link_bot').")
    print("2. Create a private Telegram channel and add BOTH bots (phone + laptop) as administrators")
    print("   with permission to post messages.\n")
    while True:
        token = input("Laptop bot token: ").strip()
        if not TOKEN_RE.match(token):
            print("  That doesn't look like a bot token (expected something like 1234567890:AAH...). Try again.")
            continue
        tg = Telegram(token)
        try:
            me = tg.get_me()
            break
        except TelegramError as exc:
            print(f"  Telegram rejected this token ({exc}). Copy it again from @BotFather.")
    print(f"OK, bot is @{me['username']}.\n")
    print("Now post any message (e.g. 'hello') in the private channel...")
    chat_id = None
    offset = None
    while chat_id is None:
        for upd in tg.get_updates(offset, timeout=20):
            offset = upd["update_id"] + 1
            post = upd.get("channel_post")
            if post:
                chat_id = post["chat"]["id"]
                print(f"Found channel '{post['chat'].get('title')}' with id {chat_id}")
                break
    if offset is not None:
        tg.get_updates(offset, timeout=0)   # acknowledge so the setup post isn't opened later
    device = input("Name for this device [laptop]: ").strip() or "laptop"
    path.write_text(json.dumps({"bot_token": token, "chat_id": chat_id, "device_name": device,
                                "open_links": True, "sync_clipboard": True}, indent=2))
    print(f"\nSaved {path}. Use channel id {chat_id} in the Android app too.")


def diagnose(cfg: Config, listen_seconds: int = 90):
    """Check every link in the chain and print what the bot actually receives."""
    ok = lambda m: print(f"  [OK]   {m}")
    bad = lambda m: print(f"  [FAIL] {m}")
    tg = Telegram(cfg.bot_token)
    print("Device Link diagnostics\n")

    print("1. Bot token")
    try:
        me = tg.get_me()
        ok(f"token works, bot is @{me['username']} (id {me['id']})")
    except Exception as exc:  # noqa: BLE001
        bad(f"{exc} -> copy the LAPTOP bot token from @BotFather and run --setup")
        return

    print("2. Webhook (blocks polling if set)")
    hook = tg.get_webhook_info()
    if hook.get("url"):
        bad(f"webhook set to {hook['url']}; removing it")
        tg.call("deleteWebhook", http_timeout=15)
    else:
        ok("no webhook")
    if hook.get("pending_update_count"):
        print(f"         {hook['pending_update_count']} update(s) waiting to be read")

    print(f"3. Channel {cfg.chat_id}")
    try:
        chat = tg.get_chat(cfg.chat_id)
        ok(f"bot can see channel {chat.get('title')!r} (type {chat.get('type')})")
        if chat.get("type") != "channel":
            bad("this is not a channel; bots can't see other bots' messages in groups. Use a private channel.")
    except Exception as exc:  # noqa: BLE001
        bad(f"{exc} -> wrong chat_id, or the bot is not in the channel. Run --setup again.")
        return
    try:
        member = tg.get_chat_member(cfg.chat_id, me["id"])
        status = member.get("status")
        if status in ("administrator", "creator"):
            ok(f"bot is {status}" + ("" if member.get("can_post_messages", True)
                                    else " but CANNOT post messages (enable 'Post messages')"))
        else:
            bad(f"bot status is {status!r}; it must be an administrator to receive channel posts")
    except Exception as exc:  # noqa: BLE001
        bad(f"could not check admin status: {exc}")

    print("4. Clipboard")
    clip = Clipboard()
    if clip.available:
        before = clip.paste()
        probe = f"device-link test {int(time.time())}"
        try:
            clip.copy(probe)
            if same_text(clip.paste(), probe):
                ok("clipboard read/write works")
            else:
                bad("wrote to the clipboard but read back something else (clipboard manager interfering?)")
        except Exception as exc:  # noqa: BLE001
            bad(f"clipboard write failed: {exc}")
        finally:
            if before is not None:
                clip.copy(before)
    else:
        bad("clipboard unavailable (Linux: install xclip / wl-clipboard)")

    print(f"5. Live check: listening for {listen_seconds}s. Now:")
    print("     - type some text in the channel from Telegram, and")
    print("     - tap 'Send clipboard' in the phone notification")
    print("   (Press Ctrl+C to stop.)\n")
    seen = 0
    offset = None
    deadline = time.time() + listen_seconds
    try:
        while time.time() < deadline:
            for upd in tg.get_updates(offset, timeout=min(20, max(1, int(deadline - time.time())))):
                offset = upd["update_id"] + 1
                post = upd.get("channel_post") or {}
                text = post.get("text") or post.get("caption") or ""
                msg = parse_message(text)
                seen += 1
                where = post.get("chat", {}).get("id")
                note = "" if where == cfg.chat_id else f"  <-- different channel than config ({cfg.chat_id})"
                print(f"   received from chat {where}: kind={msg.kind or 'plain'} "
                      f"from={msg.source or 'you/channel'} {summarize(msg.payload, post)}{note}")
    except KeyboardInterrupt:
        pass
    if seen:
        print(f"\n  [OK]   received {seen} post(s). If posts 'from=phone' never show up but your own do,")
        print("         the phone app is using the wrong token or channel.")
    else:
        print("\n  [FAIL] nothing received. Check the bot is an admin of the channel and that you posted in")
        print("         that channel (not in a chat with the bot).")
    print("\nNote: posts read here are consumed; send them again once the script is running.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open links and sync clipboard with your phone via Telegram.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--setup", action="store_true", help="interactive first-time configuration")
    parser.add_argument("--diagnose", action="store_true", help="check bot, channel and clipboard, then show live posts")
    parser.add_argument("--no-clipboard", action="store_true", help="disable clipboard sync")
    parser.add_argument("--no-links", action="store_true", help="don't open shared links")
    parser.add_argument("--skip-backlog", action="store_true",
                        help="don't open links that were shared while this script was not running")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.setup:
        setup(args.config)
        return

    cfg = Config.load(args.config)
    if args.diagnose:
        diagnose(cfg)
        return
    if args.no_clipboard:
        cfg.sync_clipboard = False
    if args.no_links:
        cfg.open_links = False
    if args.skip_backlog:
        cfg.open_backlog_links = False

    app = LinkSync(Telegram(cfg.bot_token), cfg, Clipboard() if cfg.sync_clipboard else None)
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()
        print("\nStopped.")


if __name__ == "__main__":
    main()
