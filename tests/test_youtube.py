"""YouTube (google/youtube.py): reads happen right away, anything visible to viewers only through a "before -> after" preview and Tema's confirmation, an update
doesn't reset the video's other fields, upload happens in chunks, files only from allowed folders, clear errors. YouTube is replaced by a fake server on this same Mac."""
import json
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from sena import config
from sena.agents.ag_sena import tools
from sena.core import settings, status, store
from sena.google import auth, youtube

CHANNEL = {"items": [{"id": "UC1", "snippet": {"title": "Мой канал", "customUrl": "@asto", "description": "О канале", "country": "VN"},
                      "statistics": {"subscriberCount": "120", "viewCount": "5000", "videoCount": "7"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
VIDEO = {"id": "vid12345678", "snippet": {"title": "Старое название", "description": "Старое описание", "tags": ["a", "b"], "categoryId": "28", "defaultLanguage": "ru",
                                          "publishedAt": "2026-10-01T10:00:00Z"},
         "status": {"privacyStatus": "unlisted", "license": "youtube", "embeddable": True, "publicStatsViewable": False, "selfDeclaredMadeForKids": False,
                    "uploadStatus": "processed", "madeForKids": False},
         "statistics": {"viewCount": "100", "likeCount": "10", "commentCount": "3"}, "contentDetails": {"duration": "PT5M"}}


class FakeYouTube(BaseHTTPRequestHandler):
    seen = []          # (method, path with params, body)
    errors = {}        # path without params -> (code, message)
    received = {}      # upload session -> how many bytes received

    def log_message(self, *args):
        pass

    def answer(self, code, data=None, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else b""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        size = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(size) if size else b""
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            body = json.loads(raw) if raw and "json" in (self.headers.get("Content-Type") or "") else raw
        except ValueError:
            body = raw
        FakeYouTube.seen.append((self.command, self.path, body))
        if url.path in FakeYouTube.errors:
            code, message = FakeYouTube.errors[url.path]
            return self.answer(code, {"error": {"message": message}})
        p = url.path
        if p == "/youtube/v3/channels":
            return self.answer(200, CHANNEL)
        if p == "/youtube/v3/playlistItems" and self.command == "GET":
            return self.answer(200, {"items": [{"snippet": {"title": "Серф на Бали", "description": ""}, "contentDetails": {"videoId": "vidAAAAAAAA"}},
                                               {"snippet": {"title": "Код и кофе", "description": "про Next.js"}, "contentDetails": {"videoId": "vidBBBBBBBB"}}]})
        if p == "/youtube/v3/videos" and self.command == "GET":
            return self.answer(200, {"items": [{**VIDEO, "id": i} for i in q["id"].split(",")] if q["id"] != "missing0000" else []})
        if p == "/youtube/v3/videos" and self.command == "PUT":
            return self.answer(200, body)
        if p == "/youtube/v3/commentThreads":
            return self.answer(200, {"items": [{"snippet": {"channelId": "UC1", "totalReplyCount": 2, "topLevelComment": {"id": "Ugx12345678", "snippet": {
                "authorDisplayName": "Зритель", "textDisplay": "Супер видео!", "likeCount": 4, "publishedAt": "2026-10-02T08:00:00Z", "authorChannelId": {"value": "UC9"}}}}}]})
        if p == "/v2/reports":
            if q.get("dimensions") == "video":
                return self.answer(200, {"columnHeaders": [{"name": "video"}, {"name": "views"}], "rows": [["vidAAAAAAAA", 90], ["vidBBBBBBBB", 10]]})
            return self.answer(200, {"columnHeaders": [{"name": "views"}, {"name": "subscribersGained"}], "rows": [[100, 5]]})
        if p == "/upload/youtube/v3/videos":
            return self.answer(200, headers={"Location": f"http://127.0.0.1:{self.server.server_port}/session/s1"})
        if p == "/session/s1":
            total = int(self.headers["Content-Range"].split("/")[1])
            start, end = (int(x) for x in re.match(r"bytes (\d+)-(\d+)", self.headers["Content-Range"]).groups())
            FakeYouTube.received["s1"] = FakeYouTube.received.get("s1", 0) + len(raw)
            if end + 1 >= total:
                return self.answer(200, {"id": "NEWVID12345", "status": {"privacyStatus": "private"}})
            return self.answer(308, headers={"Range": f"bytes=0-{end}"})
        if p == "/upload/youtube/v3/thumbnails/set":
            return self.answer(200, {"items": [{}]})
        if p == "/youtube/v3/comments/setModerationStatus":
            return self.answer(204)
        if p in ("/youtube/v3/playlists", "/youtube/v3/playlistItems", "/youtube/v3/comments"):
            return self.answer(200, {"id": "NEWID12345", **(body if isinstance(body, dict) else {})})
        self.answer(404, {"error": {"message": "нет такого адреса"}})

    do_GET = do_POST = do_PUT = handle_any


class YouTube(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeYouTube)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        FakeYouTube.seen, FakeYouTube.errors, FakeYouTube.received = [], {}, {}
        for patch in (mock.patch.object(youtube, "API", self.base + "/youtube/v3"), mock.patch.object(youtube, "UPLOAD", self.base + "/upload/youtube/v3"),
                      mock.patch.object(youtube, "ANALYTICS", self.base + "/v2/reports"), mock.patch.object(auth, "access_token", return_value="tok")):
            patch.start()
            self.addCleanup(patch.stop)
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        tools.pending.clear()

    def writes(self):
        return [(m, p.split("?")[0]) for m, p, _ in FakeYouTube.seen if m != "GET"]

    # ---------- reading ----------

    def test_channel_video_list_details_and_comments(self):
        self.assertIn("@asto", youtube.youtube_channel())
        rows = youtube.youtube_videos(limit=5)
        self.assertIn("https://youtu.be/vidAAAAAAAA", rows)
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", rows)   # titles and comments are written by people
        self.assertIn("Старое описание", youtube.youtube_video("vid12345678"))
        comments = youtube.youtube_comments("vid12345678", limit=10)
        self.assertIn("Супер видео!", comments)
        self.assertIn("Ugx12345678", comments)   # the id, so a reply is possible

    def test_search_by_word_uses_the_cheap_uploads_list(self):
        self.assertIn("vidBBBBBBBB", youtube.youtube_videos(query="next.js"))
        self.assertNotIn("search", " ".join(p for _, p, _ in FakeYouTube.seen))   # YouTube's own search costs a hundred quota units
        details = [p for _, p, _ in FakeYouTube.seen if p.startswith("/youtube/v3/videos?")]
        self.assertEqual(len(details), 1)
        self.assertIn("id=vidBBBBBBBB", details[0])   # details were requested only for the video that was found

    def test_stats_total_and_top_with_titles(self):
        text = youtube.youtube_stats(days=7)
        self.assertIn("subscribersGained", text)
        self.assertIn("vidAAAAAAAA", text)
        self.assertIn("Старое название", text)   # titles are attached to the top videos' ids

    def test_bad_video_number_is_refused_before_any_request(self):
        for bad in ("a b", "../x", "", None):
            with self.assertRaises(youtube.YouTubeError):
                youtube.youtube_video(bad)
        self.assertEqual(FakeYouTube.seen, [])
        with self.assertRaises(youtube.YouTubeError) as caught:
            youtube.youtube_video("missing0000")
        self.assertIn("нет", str(caught.exception))

    def test_google_errors_are_explained(self):
        for message, expect in (("Request had insufficient authentication scopes.", "google_signin"), ("The request cannot be completed because you have exceeded your quota.", "квота"),
                                ("YouTube Data API v3 has not been used in project 1 before or it is disabled.", "включи"), ("The video identified by the videoId parameter has disabled comments (commentsDisabled)", "комментарии")):
            FakeYouTube.errors = {"/youtube/v3/channels": (403, message), "/youtube/v3/commentThreads": (403, message)}
            with self.assertRaises(youtube.YouTubeError) as caught:
                youtube.youtube_channel() if "disabled comments" not in message else youtube.youtube_comments("vid12345678")
            self.assertIn(expect, str(caught.exception).lower() if expect.islower() else str(caught.exception))

    # ---------- editing a video ----------

    def test_update_shows_before_and_after_and_writes_nothing(self):
        plan = youtube.preview("update_video", video_id="vid12345678", title="Новое название", privacy="public")
        self.assertEqual(plan["изменения"]["title"], {"было": "Старое название", "станет": "Новое название"})
        self.assertEqual(plan["изменения"]["privacy"], {"было": "unlisted", "станет": "public"})
        self.assertEqual(self.writes(), [])

    def test_update_keeps_everything_that_was_not_named(self):
        youtube.apply("update_video", video_id="vid12345678", title="Новое название")
        (_, _, body), = [x for x in FakeYouTube.seen if x[0] == "PUT"]
        self.assertEqual(body["snippet"]["title"], "Новое название")
        self.assertEqual((body["snippet"]["description"], body["snippet"]["tags"], body["snippet"]["categoryId"], body["snippet"]["defaultLanguage"]),
                         ("Старое описание", ["a", "b"], "28", "ru"))   # YouTube resets anything that wasn't sent
        self.assertEqual((body["status"]["privacyStatus"], body["status"]["license"], body["status"]["embeddable"], body["status"]["publicStatsViewable"]), ("unlisted", "youtube", True, False))
        self.assertNotIn("uploadStatus", body["status"])   # service fields are read-only

    def test_scheduled_publication_makes_the_video_private_until_then(self):
        youtube.apply("update_video", video_id="vid12345678", privacy="public", publish_at="2999-01-01T10:00:00+07:00")
        (_, _, body), = [x for x in FakeYouTube.seen if x[0] == "PUT"]
        self.assertEqual((body["status"]["privacyStatus"], body["status"]["publishAt"]), ("private", "2999-01-01T10:00:00+07:00"))

    def test_bad_fields_are_refused(self):
        for args in ({"title": "<b>x</b>"}, {"title": "x" * 101}, {"privacy": "secret"}, {"publish_at": "2001-01-01T00:00:00+00:00"}, {"publish_at": "завтра"},
                     {"tags": ["a" * 101]}, {"tags": "a,b"}, {"category_id": "abc"}, {"title": "  "}):
            with self.assertRaises(youtube.YouTubeError, msg=str(args)):
                youtube.preview("update_video", video_id="vid12345678", **args)
        with self.assertRaises(youtube.YouTubeError):
            youtube.preview("update_video", video_id="vid12345678")   # nothing to change
        self.assertEqual(self.writes(), [])

    def test_delete_and_unknown_actions_do_not_exist(self):
        for action in ("delete_video", "delete_comment", "delete_playlist", "rm"):
            with self.assertRaises(youtube.YouTubeError):
                youtube.preview(action, video_id="vid12345678")

    # ---------- upload and the rest ----------

    def test_upload_goes_in_chunks_and_is_private_by_default(self):
        folder = Path(tempfile.mkdtemp(dir=config.GENERATED_DIR))
        self.addCleanup(lambda: [f.unlink() for f in folder.iterdir()] or folder.rmdir())
        video = folder / "clip.mp4"
        video.write_bytes(b"v" * 700_000)
        plan = youtube.preview("upload_video", path=str(video), title="Мой ролик", tags=["серф"])
        self.assertEqual((plan["доступ"], plan["файл"]), ("private", "clip.mp4"))
        self.assertEqual(self.writes(), [])
        with mock.patch.object(youtube, "CHUNK", 262144):
            text = youtube.apply("upload_video", path=str(video), title="Мой ролик", tags=["серф"])
        self.assertIn("https://youtu.be/NEWVID12345", text)
        self.assertEqual(FakeYouTube.received["s1"], 700_000)   # all bytes arrived, in chunks
        init = [b for m, p, b in FakeYouTube.seen if m == "POST" and "resumable" in p][0]
        self.assertEqual((init["status"]["privacyStatus"], init["status"]["selfDeclaredMadeForKids"], init["snippet"]["title"]), ("private", False, "Мой ролик"))

    def test_a_long_upload_stops_at_the_owners_word(self):
        from sena.core import cancel
        folder = Path(tempfile.mkdtemp(dir=config.GENERATED_DIR))
        self.addCleanup(lambda: [f.unlink() for f in folder.iterdir()] or folder.rmdir())
        video = folder / "long.mp4"
        video.write_bytes(b"v" * 700_000)
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 2:
                raise cancel.Stopped("Остановлено по просьбе Темы")
        with mock.patch.object(youtube, "CHUNK", 262144), mock.patch.object(youtube.cancel, "check_stop", check), self.assertRaises(cancel.Stopped):
            youtube.apply("upload_video", path=str(video), title="Длинный ролик")
        self.assertEqual(FakeYouTube.received.get("s1"), 262144)   # one chunk went, the rest didn't

    def test_files_only_from_allowed_places_and_kinds(self):
        outside = Path(self.enterContext(tempfile.TemporaryDirectory())) / "clip.mp4"   # /var/folders: neither the desktop nor Sena's folders
        outside.write_bytes(b"v" * 100)
        inside = Path(tempfile.mkdtemp(dir=config.GENERATED_DIR))
        self.addCleanup(lambda: [f.unlink() for f in inside.iterdir()] or inside.rmdir())
        (inside / "notes.txt").write_text("x")
        (inside / "empty.mp4").write_bytes(b"")
        for path in (outside, inside / "notes.txt", inside / "empty.mp4", inside / "nope.mp4", Path("/etc/hosts"), Path.home() / ".ssh" / "id_rsa"):
            with self.assertRaises(youtube.YouTubeError, msg=str(path)):
                youtube.preview("upload_video", path=str(path), title="x")
        with self.assertRaises(youtube.YouTubeError):
            youtube.preview("set_thumbnail", video_id="vid12345678", path=str(inside / "notes.txt"))
        self.assertEqual(self.writes(), [])

    def test_senas_own_data_never_goes_up_though_it_lies_on_the_desktop(self):
        """quality Q-18: the desktop was allowed whole, and Sena's data lives on it (~/Desktop/Sena/data): a picture or a video
        among her records passed as a thumbnail or an upload. Now the rule of every exit (safety.forbidden_path) comes first."""
        private = config.STATE_DIR / "cover.png"
        private.write_bytes(b"\x89PNG" + b"0" * 100)
        self.addCleanup(private.unlink, missing_ok=True)
        made = Path(tempfile.mkdtemp(dir=config.GENERATED_DIR))
        self.addCleanup(lambda: [f.unlink() for f in made.iterdir()] or made.rmdir())
        (made / "cover.png").write_bytes(b"\x89PNG" + b"0" * 100)
        with mock.patch.object(youtube, "_roots", lambda: [config.DATA.resolve()]):   # the real layout: the data folder inside an allowed place
            with self.assertRaises(youtube.YouTubeError) as caught:
                youtube.preview("set_thumbnail", video_id="vid12345678", path=str(private))
            self.assertIn("личные данные Сены", str(caught.exception))
            self.assertEqual(youtube.preview("set_thumbnail", video_id="vid12345678", path=str(made / "cover.png"))["файл"], "cover.png")   # what she made may go
        self.assertEqual(self.writes(), [])

    def test_thumbnail_playlists_and_comments(self):
        inside = Path(tempfile.mkdtemp(dir=config.GENERATED_DIR))
        self.addCleanup(lambda: [f.unlink() for f in inside.iterdir()] or inside.rmdir())
        (inside / "cover.png").write_bytes(b"\x89PNG" + b"0" * 100)
        youtube.apply("set_thumbnail", video_id="vid12345678", path=str(inside / "cover.png"))
        youtube.apply("playlist_create", title="Серф", privacy="unlisted")
        youtube.apply("playlist_add", playlist_id="PLabcdefghij", video_id="vid12345678")
        youtube.apply("comment_reply", parent_id="Ugx12345678", text="Спасибо!")
        youtube.apply("comment_moderate", comment_id="Ugx12345678", moderation="heldForReview")
        self.assertEqual([p for _, p in self.writes()], ["/upload/youtube/v3/thumbnails/set", "/youtube/v3/playlists", "/youtube/v3/playlistItems", "/youtube/v3/comments", "/youtube/v3/comments/setModerationStatus"])
        reply = [b for m, p, b in FakeYouTube.seen if p.startswith("/youtube/v3/comments?")][0]
        self.assertEqual(reply["snippet"], {"parentId": "Ugx12345678", "textOriginal": "Спасибо!"})
        for args in ({"parent_id": "Ugx12345678"}, {"text": "x"}):
            with self.assertRaises(youtube.YouTubeError):
                youtube.preview("comment_reply", **args)
        with self.assertRaises(youtube.YouTubeError):
            youtube.preview("comment_moderate", comment_id="Ugx12345678", moderation="deleted")
        # the brain takes "status" (and "check", "next") out of every call for itself: the moderation field can't be named so
        self.assertFalse({"status", "check", "next"} & set(youtube.CHANGE_TOOL["parameters"]["properties"]))
        self.assertIn("rejected", tools.youtube_change("comment_moderate", comment_id="Ugx12345678", moderation="rejected"))

    # ---------- Tema's confirmation, permissions, the switch ----------

    def test_a_change_waits_for_tema_and_is_applied_only_in_the_next_turn(self):
        status.new_turn("да")
        answer = tools.youtube_change("update_video", video_id="vid12345678", title="Новое название")
        sid = re.search(r"\[(\w+)\]", answer).group(1)
        self.assertIn("Ещё не применено", answer)
        self.assertIn("Старое название", answer)   # the "before" value is visible right away
        self.assertEqual(self.writes(), [])
        self.assertIn("Нельзя подтвердить в том же ходе", tools.confirm_send(sid))
        status.new_turn("да")
        self.assertIn("Готово на YouTube", tools.confirm_send(sid))
        self.assertEqual(self.writes(), [("PUT", "/youtube/v3/videos")])

    def test_sign_in_state_follows_the_granted_scopes(self):
        token_file = Path(self.enterContext(tempfile.TemporaryDirectory())) / "google_token.json"
        with mock.patch.object(config, "GOOGLE_TOKEN_FILE", token_file):
            self.assertFalse(youtube.signed_in())
            store.save_json(token_file, {"refresh_token": "r", "scope": "https://www.googleapis.com/auth/gmail.modify"})
            self.assertFalse(youtube.signed_in())   # signed in, but no YouTube permissions yet: need to sign in again
            store.save_json(token_file, {"refresh_token": "r", "scope": f"https://www.googleapis.com/auth/gmail.modify {auth.YOUTUBE_SCOPE}"})
            self.assertTrue(youtube.signed_in())
            store.save_json(token_file, {"refresh_token": "r", "scope": auth.YOUTUBE_SCOPE, "expired": True})
            self.assertFalse(youtube.signed_in())
        self.assertIn(auth.YOUTUBE_SCOPE, auth.SCOPES)

    def test_switch_hides_all_youtube_tools(self):
        names = {t["name"] for t in tools.available()}
        self.assertTrue({"youtube_channel", "youtube_videos", "youtube_video", "youtube_comments", "youtube_stats", "youtube_change"} <= names)
        settings.update({"use_youtube": False})
        self.assertFalse({n for n in {t["name"] for t in tools.available()} if n.startswith("youtube_")})


if __name__ == "__main__":
    unittest.main()
