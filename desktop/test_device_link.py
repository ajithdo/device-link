import logging
import unittest

import device_link as dl

logging.disable(logging.CRITICAL)

CHAT = -100123


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.files = {}

    def send_text(self, chat_id, text):
        self.sent.append(("text", chat_id, text))

    def send_document(self, chat_id, filename, content, caption):
        self.sent.append(("doc", chat_id, caption, content))

    def download_file(self, file_id):
        return self.files[file_id]


class FakeClipboard:
    available = True

    def __init__(self, value=""):
        self.value = value

    def paste(self):
        return self.value

    def copy(self, text):
        self.value = text


def post(text, chat_id=CHAT, uid=1, **extra):
    return {"update_id": uid, "channel_post": {"chat": {"id": chat_id}, "text": text, **extra}}


def make_app(clip=""):
    tg = FakeTelegram()
    opened = []
    cfg = dl.Config(bot_token="t", chat_id=CHAT, device_name="Laptop")
    app = dl.LinkSync(tg, cfg, FakeClipboard(clip), open_url=opened.append)
    return app, tg, opened


class FakeResponse:
    status_code = 200

    def __init__(self, result):
        self._result = result

    def json(self):
        return {"ok": True, "result": self._result}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, data=None, files=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[1], json or data, timeout))
        return FakeResponse([] if url.endswith("getUpdates") else {"username": "bot"})


class TelegramClientTests(unittest.TestCase):
    """Exercise the real Telegram client so argument mix-ups surface without network access."""

    def setUp(self):
        self.session = FakeSession()
        self.tg = dl.Telegram("123:abc", self.session)

    def test_get_updates_passes_long_poll_timeout(self):
        self.tg.get_updates(5, timeout=20)
        method, params, http_timeout = self.session.calls[0]
        self.assertEqual(method, "getUpdates")
        self.assertEqual(params["timeout"], 20)
        self.assertEqual(params["offset"], 5)
        self.assertGreater(http_timeout, 20)

    def test_other_methods(self):
        self.tg.get_me()
        self.tg.send_text(CHAT, "hi")
        self.tg.send_document(CHAT, "clipboard.txt", b"x", "#clip")
        self.assertEqual([c[0] for c in self.session.calls], ["getMe", "sendMessage", "sendDocument"])
        self.assertEqual(self.session.calls[1][1]["text"], "hi")


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        text = dl.build_message("clip", "My Phone!", "hello\nworld")
        self.assertEqual(text, "#clip #from_my_phone_\nhello\nworld")
        msg = dl.parse_message(text)
        self.assertEqual((msg.kind, msg.source, msg.payload), ("clip", "my_phone_", "hello\nworld"))

    def test_plain_post(self):
        msg = dl.parse_message("look at https://example.com")
        self.assertIsNone(msg.kind)
        self.assertEqual(msg.payload, "look at https://example.com")

    def test_hashtag_without_kind_is_plain(self):
        msg = dl.parse_message("#news\nhttps://example.com")
        self.assertIsNone(msg.kind)

    def test_extract_urls(self):
        text = "See (https://a.com/x?y=1). Also http://b.org, and https://a.com/x?y=1"
        self.assertEqual(dl.extract_urls(text), ["https://a.com/x?y=1", "http://b.org"])

    def test_ignores_non_http(self):
        self.assertEqual(dl.extract_urls("file:///etc/passwd javascript:alert(1)"), [])


class SyncTests(unittest.TestCase):
    def test_opens_link_from_phone(self):
        app, _, opened = make_app()
        app.handle_updates([post("#link #from_phone\nhttps://example.com/a")])
        self.assertEqual(opened, ["https://example.com/a"])

    def test_opens_plain_post_urls(self):
        app, _, opened = make_app()
        app.handle_updates([post("check https://example.com")])
        self.assertEqual(opened, ["https://example.com"])

    def test_ignores_other_chats(self):
        app, _, opened = make_app()
        app.handle_updates([post("https://evil.example", chat_id=42)])
        self.assertEqual(opened, [])

    def test_ignores_own_posts(self):
        app, _, opened = make_app()
        app.handle_updates([post("#link #from_laptop\nhttps://example.com")])
        self.assertEqual(opened, [])

    def test_backlog_links_can_be_skipped(self):
        app, _, opened = make_app()
        app.cfg.open_backlog_links = False
        app.handle_updates([post("#link #from_phone\nhttps://example.com")], backlog=True)
        self.assertEqual(opened, [])

    def test_only_latest_clip_applied_and_not_echoed(self):
        app, tg, _ = make_app()
        app.handle_updates([post("#clip #from_phone\none", uid=1), post("#clip #from_phone\ntwo", uid=2)])
        self.assertEqual(app.clipboard.value, "two")
        self.assertFalse(app.check_clipboard())
        self.assertEqual(tg.sent, [])

    def test_clip_document(self):
        app, tg, _ = make_app()
        tg.files["f1"] = "big text".encode()
        app.handle_updates([post(None, caption="#clip #from_phone", document={"file_id": "f1", "file_size": 8})])
        self.assertEqual(app.clipboard.value, "big text")

    def test_local_change_is_sent_once(self):
        app, tg, _ = make_app()
        app.clipboard.value = "copied on laptop"
        self.assertTrue(app.check_clipboard())
        self.assertFalse(app.check_clipboard())
        self.assertEqual(tg.sent, [("text", CHAT, "#clip #from_laptop\ncopied on laptop")])

    def test_long_clip_sent_as_document(self):
        app, tg, _ = make_app()
        app.clipboard.value = "x" * (dl.MAX_MESSAGE_LEN + 1)
        app.check_clipboard()
        self.assertEqual(tg.sent[0][0], "doc")
        self.assertEqual(tg.sent[0][2], "#clip #from_laptop")

    def test_whitespace_not_sent(self):
        app, tg, _ = make_app()
        app.clipboard.value = "   "
        self.assertFalse(app.check_clipboard())

    def test_plain_text_goes_to_clipboard(self):
        app, _, opened = make_app()
        app.handle_updates([post("typed in the channel")])
        self.assertEqual(app.clipboard.value, "typed in the channel")
        self.assertEqual(opened, [])

    def test_plain_text_with_link_is_opened_not_copied(self):
        app, _, opened = make_app("before")
        app.handle_updates([post("see https://example.com")])
        self.assertEqual(opened, ["https://example.com"])
        self.assertEqual(app.clipboard.value, "before")

    def test_plain_text_to_clipboard_can_be_disabled(self):
        app, _, _ = make_app("before")
        app.cfg.plain_text_to_clipboard = False
        app.handle_updates([post("typed")])
        self.assertEqual(app.clipboard.value, "before")

    def test_clipboard_write_failure_does_not_lose_links(self):
        app, _, opened = make_app()

        def broken(text):
            raise RuntimeError("OpenClipboard failed")

        app.clipboard.copy = broken
        app.handle_updates([post("https://example.com", uid=1), post("#clip #from_phone\nx", uid=2)])
        self.assertEqual(opened, ["https://example.com"])
        self.assertIsNone(app._last_clip)   # not marked as synced, so a later copy still works

    def test_crlf_echo_is_not_resent(self):
        app, tg, _ = make_app()
        app.handle_updates([post("#clip #from_phone\nline1\nline2")])
        app.clipboard.value = "line1\r\nline2"     # how Windows hands it back
        self.assertFalse(app.check_clipboard())
        self.assertEqual(tg.sent, [])


class ClipboardRetryTests(unittest.TestCase):
    def test_retries_until_clipboard_is_free(self):
        calls = []

        class FlakyPyperclip:
            def copy(self, text):
                calls.append(text)
                if len(calls) < 3:
                    raise RuntimeError("OpenClipboard failed")

        clip = dl.Clipboard.__new__(dl.Clipboard)
        clip._pc = FlakyPyperclip()
        clip.copy("hi")
        self.assertEqual(len(calls), 3)


class HintTests(unittest.TestCase):
    def test_conflict_hint(self):
        hint = dl.polling_hint(dl.TelegramError("getUpdates: Conflict: terminated by other getUpdates request"))
        self.assertIn("OTHER bot", hint)

    def test_webhook_hint(self):
        hint = dl.polling_hint(dl.TelegramError("getUpdates: Conflict: can't use getUpdates method while webhook is active"))
        self.assertIn("--diagnose", hint)


if __name__ == "__main__":
    unittest.main()
