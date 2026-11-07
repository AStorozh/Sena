"""Images and video through the Gemini API: requests, response parsing, limits, clear errors. The network is replaced with stubs."""
import base64
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.agents.ag_sena import tools
from sena.core import store
from sena.google import media
from sena.telegram import api

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def reply(data=None, status=200, content=b""):
    return SimpleNamespace(status_code=status, json=lambda: data, text=json.dumps(data) if data else "", content=content)


def image_result(b64=None):
    return {"id": "v1_x", "status": "completed", "steps": [
        {"type": "user_input", "content": [{"type": "text", "text": "..."}]},
        {"type": "model_output", "content": [{"type": "text", "text": "Вот картинка"},
                                             {"type": "image", "mime_type": "image/png", "data": b64 or base64.b64encode(PNG).decode()}]}]}


class Gemini(unittest.TestCase):
    def setUp(self):
        self.key = mock.patch.object(config, "GEMINI_API_KEY", "test-key")
        self.key.start()
        config.USAGE_FILE.unlink(missing_ok=True)

    def tearDown(self):
        self.key.stop()

    def test_image_request_and_saved_file(self):
        with mock.patch.object(media.requests, "request", return_value=reply(image_result())) as call:
            path = media.generate_image("a red fox in snow", aspect_ratio="16:9", size="2K")
        sent = call.call_args
        self.assertEqual(sent.args, ("POST", f"{media.API}/interactions"))
        self.assertEqual(sent.kwargs["headers"]["x-goog-api-key"], "test-key")
        body = sent.kwargs["json"]
        self.assertEqual(body["model"], "gemini-nano-banana-2.1")
        self.assertEqual(body["input"], [{"type": "text", "text": "a red fox in snow"}])
        self.assertEqual(body["response_format"], {"type": "image", "aspect_ratio": "16:9", "image_size": "2K"})
        self.assertEqual(path.read_bytes(), PNG)
        self.assertTrue(str(path).startswith(str(config.GENERATED_DIR)))

    def test_reference_images_are_sent_for_editing(self):
        ref = config.INBOX_DIR / "cat.png"
        ref.write_bytes(PNG)
        with mock.patch.object(media.requests, "request", return_value=reply(image_result())) as call:
            media.generate_image("add a hat", tier="pro", reference_images=[str(ref)])
        body = call.call_args.kwargs["json"]
        self.assertEqual(body["model"], "gemini-3-pro-image")
        self.assertEqual([p["type"] for p in body["input"]], ["text", "image"])
        self.assertEqual(base64.b64decode(body["input"][1]["data"]), PNG)

    def test_response_shape_is_found_anywhere(self):
        flat = {"output_image": {"type": "image", "mime_type": "image/png", "data": base64.b64encode(PNG).decode()}}
        with mock.patch.object(media.requests, "request", return_value=reply(flat)):
            self.assertEqual(media.generate_image("x").read_bytes(), PNG)

    def test_no_image_in_answer_explains(self):
        refusal = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "Не могу это нарисовать"}]}]}
        with mock.patch.object(media.requests, "request", return_value=reply(refusal)):
            with self.assertRaises(media.MediaError) as e:
                media.generate_image("x")
        self.assertIn("Не могу это нарисовать", str(e.exception))

    def test_missing_key_and_billing_errors_are_explained(self):
        with mock.patch.object(config, "GEMINI_API_KEY", ""):
            with self.assertRaises(media.MediaError) as e:
                media.generate_image("x")
            self.assertIn("GEMINI_API_KEY", str(e.exception))
        blocked = reply({"error": {"message": "Image generation is only available on the paid tier. Enable billing."}}, status=403)
        with mock.patch.object(media.requests, "request", return_value=blocked):
            with self.assertRaises(media.MediaError) as e:
                media.generate_image("x")
        self.assertIn("платный тариф", str(e.exception))

    def test_daily_limit_stops_spending(self):
        with mock.patch.dict(media.LIMITS, {"image": 2}):
            store.save_json(config.USAGE_FILE, {"date": __import__("datetime").date.today().isoformat(), "image": 2, "video": 0})
            with mock.patch.object(media.requests, "request", side_effect=AssertionError("запрос не должен уходить")):
                with self.assertRaises(media.MediaError) as e:
                    media.generate_image("x")
            self.assertIn("Дневной лимит", str(e.exception))

    def test_no_limit_by_default(self):
        self.assertEqual(media.LIMITS, {"image": None, "video": None})
        store.save_json(config.USAGE_FILE, {"date": __import__("datetime").date.today().isoformat(), "image": 10_000})
        with mock.patch.object(media.requests, "request", return_value=reply(image_result())):
            media.generate_image("x")   # no limit set, the counter keeps going
        self.assertEqual(store.load_json(config.USAGE_FILE, {})["image"], 10_001)

    def test_counter_grows_per_success_only(self):
        with mock.patch.object(media.requests, "request", return_value=reply(image_result())):
            media.generate_image("a")
            media.generate_image("b")
        self.assertEqual(store.load_json(config.USAGE_FILE, {})["image"], 2)
        with mock.patch.object(media.requests, "request", return_value=reply({"error": {"message": "boom"}}, status=500)):
            with self.assertRaises(media.MediaError):
                media.generate_image("c")
        self.assertEqual(store.load_json(config.USAGE_FILE, {})["image"], 2)

    def test_video_waits_for_file_then_downloads(self):
        states = iter([{"state": "PROCESSING"}, {"state": "ACTIVE"}])

        def fake(method, url, **kw):
            if url.endswith("/interactions"):
                self.assertEqual(kw["json"]["response_format"]["delivery"], "uri")
                return reply({"steps": [{"type": "model_output", "content": [{"type": "video", "uri": f"{media.API}/files/abc-123"}]}]})
            if url.endswith(":download"):
                return reply(content=b"MP4DATA")
            return reply(next(states))
        with mock.patch.object(media.requests, "request", fake), mock.patch.object(media.time, "sleep", lambda s: None):
            path = media.generate_video("waves on a beach", aspect_ratio="9:16")
        self.assertEqual(path.read_bytes(), b"MP4DATA")
        self.assertEqual(path.suffix, ".mp4")

    def test_video_failure_is_reported(self):
        def fake(method, url, **kw):
            if url.endswith("/interactions"):
                return reply({"output_video": {"type": "video", "uri": f"{media.API}/files/zzz"}})
            return reply({"state": "FAILED"})
        with mock.patch.object(media.requests, "request", fake), mock.patch.object(media.time, "sleep", lambda s: None):
            with self.assertRaises(media.MediaError) as e:
                media.generate_video("x")
        self.assertIn("не смог сделать видео", str(e.exception))

    def test_only_a_picture_that_may_leave_the_mac_goes_as_a_sample(self):
        """Any file the model named went to Google as "image/jpeg": Sena's memory, a key, a document."""
        memory = config.DATA / "memory" / "notes-for-test.png"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_bytes(PNG)
        text = config.INBOX_DIR / "notes.txt"
        text.write_text("не картинка")
        with mock.patch.object(media.requests, "request", side_effect=AssertionError("запрос не должен уходить")):
            for path in (memory, text, config.INBOX_DIR / "nope.png", "~/.ssh/id_rsa"):
                with self.assertRaises(media.MediaError, msg=str(path)):
                    media.generate_image("x", reference_images=[str(path)])
            with self.assertRaises(media.MediaError):
                media.generate_video("x", reference_image=str(memory))

    def test_a_blip_while_the_video_renders_does_not_lose_it_and_a_free_read_is_never_unpriced(self):
        answers = iter([{"state": "PROCESSING"}, media.requests.ConnectionError("reset"), {"state": "ACTIVE"}])

        def fake(method, url, **kw):
            if url.endswith("/interactions"):
                return reply({"steps": [{"type": "model_output", "content": [{"type": "video", "uri": f"{media.API}/files/abc-123"}]}]})
            if url.endswith(":download"):
                return reply(content=b"MP4DATA")
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return reply(answer)
        with mock.patch.object(media.requests, "request", fake), mock.patch.object(media.time, "sleep", lambda s: None), \
                mock.patch.object(media.costs, "record") as record:
            path = media.generate_video("waves")
        self.assertEqual(path.read_bytes(), b"MP4DATA")
        lost = [c for c in record.call_args_list if c.kwargs.get("purpose") == "сбой связи с Google"]
        self.assertEqual(lost, [])   # the failed GET was free: no extra "unpriced" line
        with mock.patch.object(media.requests, "request", side_effect=media.requests.ConnectionError("reset")), \
                mock.patch.object(media.costs, "record") as record:
            with self.assertRaises(media.MediaError):
                media.generate_image("x")
        self.assertEqual(record.call_args.kwargs.get("purpose"), "сбой связи с Google")   # the generating call may have been charged

    def test_bad_parameters_are_rejected_before_any_request(self):
        with mock.patch.object(media.requests, "request", side_effect=AssertionError("запрос не должен уходить")):
            for call in (lambda: media.generate_image("x", aspect_ratio="7:3"), lambda: media.generate_image("x", size="8K"),
                         lambda: media.generate_video("x", resolution="4k")):
                with self.assertRaises(media.MediaError):
                    call()


class ThroughCoordinator(unittest.TestCase):
    def test_image_is_sent_to_telegram_and_reported(self):
        sent = []
        with mock.patch.object(config, "GEMINI_API_KEY", "k"), \
             mock.patch.object(media.requests, "request", return_value=reply(image_result())), \
             mock.patch.object(api, "send_media", lambda path, caption="": sent.append((str(path), caption)) or "Отправлено в Телеграм"):
            config.USAGE_FILE.unlink(missing_ok=True)
            out = tools.make_image("мост на рассвете", aspect_ratio="16:9")
        self.assertIn("Картинка готова", out)
        self.assertIn("Отправлено в Телеграм", out)
        self.assertEqual(sent[0][1], "мост на рассвете")


if __name__ == "__main__":
    unittest.main()
