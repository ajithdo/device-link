import unittest

import device_link as dl

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


if __name__ == "__main__":
    unittest.main()
