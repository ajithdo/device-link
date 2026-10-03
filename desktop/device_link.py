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

    def call(self, method: str, timeout: float = 30, files=None, **params):
        while True:
            if files:
                resp = self.session.post(self._url(method), data=params, files=files, timeout=timeout)
            else:
                resp = self.session.post(self._url(method), json=params, timeout=timeout)
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
        return self.call("getMe", timeout=15)

    def get_updates(self, offset: Optional[int], timeout: int = 25):
        params = {"timeout": timeout, "allowed_updates": ["channel_post"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", timeout=timeout + 10, **params)

    def send_text(self, chat_id, text: str):
        return self.call("sendMessage", timeout=20, chat_id=chat_id, text=text,
                         disable_web_page_preview=True, disable_notification=True)

    def send_document(self, chat_id, filename: str, content: bytes, caption: str):
        files = {"document": (filename, io.BytesIO(content), "text/plain")}
        return self.call("sendDocument", timeout=60, files=files, chat_id=chat_id,
                         caption=caption, disable_notification="true")

    def download_file(self, file_id: str) -> bytes:
        info = self.call("getFile", timeout=20, file_id=file_id)
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

    def copy(self, text: str):
        self._pc.copy(text)


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
        """Process a batch of updates. Only the newest clip in a batch is applied."""
        latest_clip = None
        for upd in updates:
            post = upd.get("channel_post")
            if not post or post.get("chat", {}).get("id") != self.cfg.chat_id:
                continue
            text = post.get("text") or post.get("caption") or ""
            msg = parse_message(text)
            if msg.source == self.me:
                continue
            if msg.kind == KIND_CLIP:
                latest_clip = (post, msg)
            elif msg.kind == KIND_LINK or msg.kind is None:
                if backlog and not self.cfg.open_backlog_links:
                    continue
                self._open_links(msg.payload)
        if latest_clip:
            self._apply_clip(*latest_clip)

    def _open_links(self, text: str):
        if not self.cfg.open_links:
            return
        for url in extract_urls(text):
            log.info("Opening %s", url)
            self.open_url(url)

    def _apply_clip(self, post: dict, msg: Message):
        if not (self.cfg.sync_clipboard and self.clipboard and self.clipboard.available):
            return
        text = msg.payload
        doc = post.get("document")
        if doc:
            if doc.get("file_size", 0) > MAX_CLIP_BYTES:
                log.warning("Ignoring oversized clipboard document")
                return
            text = self.tg.download_file(doc["file_id"]).decode("utf-8", errors="replace")
        with self._lock:
            self._last_clip = text
            self.clipboard.copy(text)
        log.info("Clipboard updated from %s (%d chars)", msg.source or "channel", len(text))

    # ---- outgoing

    def check_clipboard(self):
        """Send the local clipboard if it changed since the last sync. Returns True if sent."""
        current = self.clipboard.paste()
        if current is None or not current.strip():
            return False
        with self._lock:
            if current == self._last_clip:
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

        offset = None
        first = True
        backoff = 1
        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(offset)
                backoff = 1
            except (TelegramError, requests.RequestException) as exc:
                log.warning("Polling failed: %s (retrying in %ss)", exc, backoff)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open links and sync clipboard with your phone via Telegram.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--setup", action="store_true", help="interactive first-time configuration")
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
