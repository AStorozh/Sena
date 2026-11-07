"""Long works: pursued turn after turn until the judge sees a concrete fact that meets done-when; the owner hears only what
matters (done, a question, the budget, a failure); pause, resume and stop are his; a turn cut by a restart is marked and
the next one checks first; nothing runs past the work's budget."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.agents.ag_sena import work
from sena.core import moment, store
from sena.phone import panels


def verdict(v, reason="причина", message="", wait=0, progress="result"):
    return SimpleNamespace(output_text=json.dumps({"reason": reason, "verdict": v, "progress": progress, "missing": [], "wait_minutes": wait,
                                                   "message": message}), output=[], id="j")


class Works(unittest.TestCase):
    def setUp(self):
        store.clear(config.WORKS_FILE)
        work._running.clear()
        self.told, self.turns, self.verdicts = [], [], []
        self.answers = []
        work.start_engine(self.runner, self.told.append)
        self.patches = [mock.patch.object(work.safety, "background", lambda f, *a, what=None: f(*a)),   # turns run inline
                        mock.patch.object(work.llm, "respond", lambda **kw: self.verdicts.pop(0)),
                        mock.patch.object(work.costs, "by_task", lambda ids, since=None: {i: 0.5 for i in ids})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        work.start_engine(None, None)

    def runner(self, w):
        self.turns.append(dict(w))
        answer = self.answers.pop(0) if self.answers else "Сделано: шаг. Дальше: следующий шаг."
        if callable(answer):
            answer = answer(w)
        text, outcome = answer if isinstance(answer, tuple) else (answer, "ok")
        tid = f"t{len(self.turns)}"
        work.turn_began(w["id"], tid)
        return text, tid, outcome

    def new(self, **kw):
        reply = work.start(kw.pop("title", "Сайт бутылок"), kw.pop("goal", "сделать сайт по продаже стеклянных бутылок"),
                           kw.pop("done_when", "сайт открывается по адресу и в нём есть каталог"), **kw)
        return reply.split("[")[1].split("]")[0]

    def test_a_work_goes_turn_by_turn_until_the_judge_sees_the_fact(self):
        wid = self.new(plan=["требования", "вёрстка", "выкладка"], budget_usd=10, max_turns=5)
        self.verdicts = [verdict("continue"), verdict("done", "адрес открывается", "Сайт готов: bottles.example")]

        def second(w):
            work.claim_done(w["id"], "https://bottles.example открывается, каталог на месте")
            return "Сделано: выложено. Дальше: ничего."
        self.answers = ["Сделано: требования. Дальше: вёрстка.", second]
        self.assertEqual(work.tick(), 1)
        w = work.get(wid)
        self.assertEqual((w["status"], w["turns"], w["tasks"]), (work.ACTIVE, 1, ["t1"]))
        self.assertIn("требования", w["checkpoint"])
        self.assertEqual(self.told, [])   # a turn that just moved on tells the owner nothing
        work.tick()
        w = work.get(wid)
        self.assertEqual(w["status"], work.DONE)
        self.assertIn("bottles.example", w["claim"])
        self.assertEqual(self.told, ["Работа «Сайт бутылок» сделана. Сайт готов: bottles.example"])
        self.assertIn("Итог прошлого хода:\nСделано: требования", work.brief(self.turns[1]))   # the second turn started from the first one's report
        self.assertIn("ход 2 из 5", work.brief(self.turns[1]))
        self.assertEqual(work.tick(), 0)   # done: no more turns

    def test_what_a_waiting_work_prepared_waits_with_its_question_and_comes_with_buttons(self):
        """Dify keeps a human-input form 36 hours: a work's prepared mail died after 15 minutes, and a "yes" after another
        message was refused; the question came without buttons."""
        from sena.core import confirm, status
        self.new()
        made = {}

        def turn(w):
            status.local.turn = 7
            made["sid"] = confirm.hold("письмо olga@x", "акты за октябрь", lambda: "Письмо отправлено")
            confirm.pending[made["sid"]]["tid"] = "t1"   # prepared by this turn's task
            work.need_owner(w["id"], "Отправить Ольге акты за октябрь?")
            return "Подготовила письмо, нужно «да» Темы."
        self.answers = [turn]
        self.verdicts = [verdict("need_owner", "нужно «да» на письмо")]
        sent = []
        work.start_engine(self.runner, lambda text, held=(): sent.append((text, list(held))))
        work.tick()
        self.assertEqual(sent[-1][1], [(made["sid"], "письмо olga@x")])   # the question comes with its button
        item = confirm.pending[made["sid"]]
        item["time"] -= 3600   # an hour later
        status.local.turn, status.local.said = 10, "да, отправляй"   # and after other messages: his own «да»
        done, result, _ = confirm.confirm(made["sid"])
        self.assertTrue(done)
        self.assertEqual(result, "Письмо отправлено")

    def test_a_work_waiting_for_the_owner_is_reminded_once_and_paused_after_a_week(self):
        """n8n's waits time out: a work waiting for an answer sat there for good, with nobody reminded."""
        wid = self.new()
        work._change(wid, lambda w: w.update(status=work.WAITING, question="какой домен взять?", waiting_since=moment.now() - work.WAIT_REMIND - 10))
        work.tick()
        work.tick()
        self.assertEqual(sum("всё ещё ждёт тебя" in t for t in self.told), 1)
        work._change(wid, lambda w: w.update(waiting_since=moment.now() - work.WAIT_PAUSE - 10))
        work.tick()
        self.assertEqual(work.get(wid)["status"], work.PAUSED)
        self.assertIn("неделю ждала", self.told[-1])

    def test_a_done_work_resumed_with_a_new_request_is_not_done_on_the_old_proof(self):
        """The A2A study: a finished work restarted in place kept its old "done" fact, and its judge never saw the owner's new
        words — the new request could be declared done on the old proof."""
        wid = self.new()
        work._change(wid, lambda w: w.update(status=work.DONE, claim="https://bottles.example открывается"))
        work.control(wid, "resume", "добавь страницу оплаты")
        self.assertIsNone(work.get(wid)["claim"])
        asked = []

        def respond(**kw):
            asked.append(kw["input"])
            return SimpleNamespace(output_text=json.dumps({"reason": "оплаты ещё нет", "verdict": "continue", "missing": [], "wait_minutes": 0,
                                                           "message": ""}), output=[], id="j")
        with mock.patch.object(work.llm, "respond", respond):
            work.tick()
        self.assertIn("Что Тема сказал по этой работе", asked[0])
        self.assertIn("добавь страницу оплаты", asked[0])
        self.assertNotIn("bottles.example открывается", asked[0])

    def test_the_judge_reads_what_the_tools_showed_and_the_next_turn_hears_whats_missing(self):
        from sena.core import events
        self.new()
        asked = []

        def respond(**kw):
            asked.append(kw["input"])
            return SimpleNamespace(output_text=json.dumps({"reason": "адреса нет", "verdict": "continue", "missing": ["сайт открывается по адресу"],
                                                           "wait_minutes": 0, "message": ""}), output=[], id="j")

        def turn(w):
            events.log("tool", "Сайт собран: dist/index.html", name="computer_task", tid="t1")
            return "Сделано: собрала сайт. Дальше: выложить."
        self.answers = [turn]
        with mock.patch.object(work.llm, "respond", respond):
            work.tick()
        self.assertIn("Что показали инструменты в этом ходе", asked[0])
        self.assertIn("computer_task: Сайт собран: dist/index.html", asked[0])
        self.assertIn("слова отчёта и заявление — не доказательство", work.JUDGE)
        self.verdicts = [verdict("continue")]
        work.tick()
        self.assertIn("Проверка не нашла доказательств для: сайт открывается по адресу", work.brief(self.turns[1]))

    def test_a_claim_the_judge_rejects_is_not_done(self):
        wid = self.new()
        self.verdicts = [verdict("continue", "факта нет")]
        self.answers = [lambda w: work.claim_done(w["id"], "вроде всё готово") and "Сделано: всё."]
        work.tick()
        w = work.get(wid)
        self.assertEqual(w["status"], work.ACTIVE)
        self.assertIsNone(w["claim"])   # the next turn has to prove it again

    def test_a_question_waits_for_the_owner_and_his_answer_resumes(self):
        wid = self.new()
        self.verdicts = [verdict("continue")]
        self.answers = [lambda w: work.need_owner(w["id"], "Какой домен покупать: bottles.ru или bottles.shop?") and "Мешает: нужен домен."]
        work.tick()
        self.assertEqual(work.get(wid)["status"], work.WAITING)
        self.assertIn("ждёт тебя: Какой домен", self.told[0])
        self.assertEqual(work.tick(), 0)
        self.assertIn("ждёт ответа: Какой домен", work.context())
        work.control(wid, "resume", "бери bottles.shop")
        w = work.get(wid)
        self.assertEqual((w["status"], w["question"]), (work.ACTIVE, ""))
        self.verdicts = [verdict("continue")]
        work.tick()
        self.assertIn("бери bottles.shop", work.brief(self.turns[-1]))

    def test_turns_without_a_result_ask_for_a_new_plan_then_for_the_owner(self):
        """crewAI: reading and searching are not progress; two such turns in a row — a new plan, three — the owner is asked."""
        wid = self.new()
        self.verdicts = [verdict("continue", "только смотрела файлы", progress="explored") for _ in range(3)]
        work.tick()
        self.assertNotIn("прежний путь не работает", work.brief(work.get(wid)))
        work.tick()
        self.assertIn("прежний путь не работает", work.brief(work.get(wid)))   # the third turn starts with "plan it anew"
        work.tick()
        w = work.get(wid)
        self.assertEqual(w["status"], work.WAITING)
        self.assertIn("3 хода подряд без результата", self.told[-1])
        work.control(wid, "resume", "попробуй через GitHub Pages")
        self.assertEqual(work.get(wid)["idle"], 0)

    def test_the_judges_diagnosis_and_chosen_fix_reach_the_next_turn(self):
        """CAMEL's study: the judge's reason never reached the next turn, every idle turn got the same generic line."""
        wid = self.new()
        v = verdict("continue", "сайт отдаёт 502 после выкладки", progress="none")
        v_text = json.loads(v.output_text)
        v_text["recovery"] = "retry"
        self.verdicts = [SimpleNamespace(output_text=json.dumps(v_text, ensure_ascii=False), output=[], id="j")]
        work.tick()
        shown = work.brief(work.get(wid))
        self.assertIn("сайт отдаёт 502 после выкладки", shown)
        self.assertIn("повтори тот же шаг", shown)

    def test_a_new_plan_keeps_the_finished_steps_and_their_facts(self):
        """MetaGPT keeps the finished prefix of a plan: a re-plan here erased done steps (67 % to 0)."""
        wid = self.new()
        work.set_plan(wid, [{"text": "собрать сайт", "status": "done", "fact": "dist/index.html"}, {"text": "выложить", "status": "doing"}])
        refused = work.set_plan(wid, [{"text": "выложить на Vercel", "status": "doing"}])
        self.assertIsInstance(refused, work.Failure)
        self.assertIn("собрать сайт", refused)
        work.set_plan(wid, [{"text": "собрать сайт", "status": "done"}, {"text": "выложить на Vercel", "status": "doing"}])
        self.assertIn("собрать сайт — dist/index.html", work.brief(work.get(wid)))   # the fact stayed though the plan came without it

    def test_one_result_among_idle_turns_doesnt_hide_a_stuck_work(self):
        """Magentic-One's stall counter decays: explored, explored, result went on forever and never asked the owner."""
        wid = self.new()
        self.verdicts = [verdict("continue", "смотрела", progress=p) for p in ("explored", "explored", "result", "explored", "explored")]
        for _ in range(5):
            work.tick()
        self.assertEqual(work.get(wid)["status"], work.WAITING)

    def test_a_done_nobody_claimed_needs_a_second_vote(self):
        """MetaGPT votes: one fast judge call ended a work as done when the turn itself claimed nothing."""
        wid = self.new()
        self.verdicts = [verdict("done", "кажется, всё готово", "Готово"), verdict("continue", "адреса нет"), verdict("continue", "адреса нет")]
        work.tick()
        self.assertEqual(work.get(wid)["status"], work.ACTIVE)   # outvoted: the work goes on
        work.control(wid, "stop")
        wid2 = self.new(title="Вторая")
        self.verdicts = [verdict("done", "адрес открывается", "Готово"), verdict("done", "адрес открывается", "Готово")]
        work.tick()
        self.assertEqual(work.get(wid2)["status"], work.DONE)   # two agree: done

    def test_a_cut_turns_steps_reach_the_next_turn_without_foreign_text(self):
        """Microsoft Agent Framework replays from a checkpoint; here a cut turn's steps were invisible — only confirmed sends showed."""
        from sena.core import events
        wid = self.new()
        events.log("tool", "Записан файл dist/index.html", name="computer_task", tid="cut-1")
        events.log("tool", "<<<ВНЕШНИЕ ДАННЫЕ x | сайт>>>\nтекст страницы\n<<<КОНЕЦ ВНЕШНИХ ДАННЫХ x>>>", name="browser_task", tid="cut-1")
        work._change(wid, lambda w: w.update(interrupted=True, cut_tid="cut-1"))
        shown = work.brief(work.get(wid))
        self.assertIn("computer_task: Записан файл dist/index.html", shown)
        self.assertIn("browser_task: (чужой текст", shown)
        self.assertNotIn("текст страницы", shown)

    def test_the_budget_and_the_turns_are_hard_limits(self):
        wid = self.new(max_turns=2, budget_usd=10)
        self.verdicts = [verdict("continue"), verdict("continue")]
        work.tick()
        work.tick()
        w = work.get(wid)
        self.assertEqual(w["status"], work.LIMITED)
        self.assertIn("кончились ходы (2 из 2", self.told[-1])   # the turns ran out, not the money: he is asked for the right thing
        self.assertEqual(work.tick(), 0)
        work.control(wid, "resume", max_turns=1)
        self.assertEqual(work.get(wid)["max_turns"], 3)
        wid2 = self.new(title="Почта", budget_usd=0.4)   # each fake turn costs 0.5 $
        self.verdicts = [verdict("continue"), verdict("continue")]
        work.tick()
        self.assertEqual(work.get(wid2)["status"], work.LIMITED)
        self.assertIn("кончился бюджет", self.told[-1])

    def test_go_on_after_the_limits_really_goes_on(self):
        """The owner added money to a work whose turns had run out: it said "снова в работе" and was silently limited again."""
        wid = self.new(max_turns=1, budget_usd=10)
        self.verdicts = [verdict("continue")]
        work.tick()
        self.assertEqual(work.get(wid)["status"], work.LIMITED)
        work.control(wid, "resume", budget_usd=2)   # no number of turns: as many again as a new work gets
        w = work.get(wid)
        self.assertEqual((w["status"], w["max_turns"]), (work.ACTIVE, 1 + work.MAX_TURNS))
        wid2 = self.new(title="Почта", budget_usd=0.4)
        self.verdicts = [verdict("continue")]
        work.tick()
        refused = work.control(wid2, "resume")   # the money ran out and none was added: resuming would stop it at once
        self.assertIsInstance(refused, work.Failure)
        self.assertEqual(work.get(wid2)["status"], work.LIMITED)
        self.assertIn("снова в работе", work.control(wid2, "resume", budget_usd=1))

    def test_a_verdict_without_its_fields_never_leaves_the_work_stuck(self):
        """A judge's answer without "reason" raised a KeyError after the turn, and the work stayed marked as running for good."""
        wid = self.new()
        self.verdicts = [SimpleNamespace(output_text=json.dumps({"verdict": "continue"}), output=[], id="j")]
        work.tick()
        self.assertNotIn(wid, work._running)
        self.assertEqual(work.get(wid)["status"], work.ACTIVE)

    def test_a_work_cannot_raise_its_own_budget(self):
        """A background turn opened the work group and gave itself 90 $ and 150 turns: the model's work_control needs his words."""
        from sena.core import status
        wid = self.new()
        status.local.said = None   # a work's own turn: no words of the owner
        refused = work.FUNCTIONS["work_control"](wid, "resume", budget_usd=90, max_turns=150)
        self.assertIsInstance(refused, work.Failure)
        self.assertEqual(work.get(wid)["budget"], work.BUDGET)
        status.local.said = "дай ей ещё 5 долларов"
        self.addCleanup(setattr, status.local, "said", None)
        self.assertIn("снова в работе", work.FUNCTIONS["work_control"](wid, "resume", budget_usd=5))
        self.assertEqual(work.control(wid, "pause")[:6], "Работа")   # the apps' buttons are his and call control directly

    def test_stop_stops_the_turn_going_now(self):
        wid = self.new()
        work._change(wid, lambda w: w.update(running=moment.now(), tasks=["turn-7"]))
        with mock.patch.object(work.cancel, "stop_task") as stop:
            work.control(wid, "stop")
        stop.assert_called_once_with("turn-7")
        self.assertEqual(work.get(wid)["status"], work.STOPPED)

    def test_a_turn_cut_by_a_restart_is_resumed_with_a_check_first(self):
        from sena.core import events
        wid = self.new()
        with work._lock:
            items = work._all()
            items[0].update(running=moment.now(), turns=1, tasks=["cut1"])
            work._save(items)
        events.log("effect", "сообщение «Маме»: буду в 7", key="k1", state="start", tid="cut1")
        events.log("effect", "сообщение «Маме»: отправлено", key="k1", state="done", tid="cut1")
        events.log("effect", "письмо olga@x: акты", key="k2", state="start", tid="cut1")   # no "done": the restart came in between
        self.assertEqual(work.resume_after_restart(), 1)
        w = work.get(wid)
        self.assertTrue(w["interrupted"])
        self.assertIsNone(w["running"])
        self.verdicts = [verdict("continue")]
        work.tick()
        brief = work.brief(self.turns[0])
        self.assertIn("прервал перезапуск", brief)
        self.assertIn("сделано: сообщение «Маме»: буду в 7", brief)
        self.assertIn("начато, исход неизвестен: письмо olga@x", brief)
        self.assertFalse(work.get(wid)["interrupted"])

    def test_the_owners_stop_pauses_and_keeps_the_last_report(self):
        wid = self.new()
        self.verdicts = [verdict("continue")]
        work.tick()
        self.answers = [("Остановлено по просьбе Темы", "cut")]
        work.tick()
        w = work.get(wid)
        self.assertEqual(w["status"], work.PAUSED)
        self.assertIn("Сделано: шаг", w["checkpoint"])
        self.assertTrue(w["interrupted"])
        self.assertEqual(work.tick(), 0)

    def test_broken_turns_pause_the_work_and_say_so(self):
        wid = self.new()
        self.answers = [("Не получилось ответить: нет связи с Claude.", "failed")] * 3
        for _ in range(3):
            with work._lock:   # no waiting between retries in the test
                items = work._all()
                items[0]["next_at"] = 0
                work._save(items)
            work.tick()
        self.assertEqual(work.get(wid)["status"], work.PAUSED)
        self.assertIn("3 хода подряд не удались", self.told[-1])

    def test_a_spent_day_budget_moves_the_work_to_tomorrow(self):
        wid = self.new()
        self.answers = [("Дневной бюджет исчерпан: потрачено $5.00 из $5.00.", "day")]
        work.tick()
        w = work.get(wid)
        self.assertEqual((w["status"], w["turns"], w["fails"]), (work.ACTIVE, 0, 0))
        self.assertGreater(w["next_at"], moment.now() + 60)
        self.assertIn("продолжится завтра", self.told[-1])

    def test_a_broken_judge_never_stops_the_work(self):
        wid = self.new()
        self.verdicts = [SimpleNamespace(output_text="не json", output=[], id="j")]
        work.tick()
        self.assertEqual(work.get(wid)["status"], work.ACTIVE)

    def test_a_judge_that_didnt_answer_is_no_progress_and_is_told_once(self):
        """Dify's study: the broken judge's stand-in answer counted as a result and reset the idle count — a stuck work looked busy."""
        wid = self.new()
        work._change(wid, lambda w: w.update(idle=1))
        for turn in range(work.JUDGE_BROKEN):
            self.verdicts = [SimpleNamespace(output_text="не json", output=[], id=f"j{turn}")]
            work._change(wid, lambda w: w.update(next_at=0))
            work.tick()
        w = work.get(wid)
        self.assertEqual(w["idle"], 1)   # neither reset as progress nor counted as idleness
        self.assertEqual(sum("проверка ходов" in t for t in self.told), 1)

    def test_owner_controls_and_the_plan(self):
        wid = self.new()
        self.assertIn("1 из 3", work.set_plan(wid, [{"text": "а", "status": "done"}, {"text": "б", "status": "doing"}, {"text": "в", "status": "doing"}]))
        self.assertEqual([s["status"] for s in work.get(wid)["plan"]], ["done", "doing", "todo"])   # one step in progress
        self.assertIn("на паузе", work.control(wid, "pause"))
        self.assertEqual(work.tick(), 0)
        self.assertIn("остановлена", work.control(wid, "stop"))
        self.assertIn("Долгих работ нет", work.listing())
        self.assertIn("Сайт бутылок", work.listing("all"))
        self.assertIn("Нужно условие готовности", work.start("Сайт", "сделать большой сайт", ""))
        self.assertIn("Работы zz нет", work.control("zz", "pause"))

    def test_at_most_two_works_step_at_once(self):
        for n in range(3):
            self.new(title=f"Работа {n}")
        with mock.patch.object(work.safety, "background", lambda f, *a, what=None: None):   # turns that don't finish
            self.assertEqual(work.tick(), 2)
            self.assertEqual(work.tick(), 0)

    def test_a_work_he_stopped_is_not_logged_as_senas_shutdown(self):
        """The audit: a work he stopped read «ход прервало выключение Сены», and its resume was told a restart cut the turn."""
        wid = self.new()

        def stopped(w):
            work.control(w["id"], "stop")
            return ("Остановлено по просьбе Темы", "cut")
        self.answers = [stopped]
        work.tick()
        w = work.get(wid)
        self.assertEqual(w["status"], work.STOPPED)
        self.assertIn("остановлен Темой", w["log"][-1]["text"])
        self.assertNotIn("выключение", w["log"][-1]["text"])
        work.control(wid, "resume", "продолжай")
        self.assertIn("Прошлый ход остановил Тема", work.brief(work.get(wid)))
        self.assertNotIn("перезапуск", work.brief(work.get(wid)))

    def test_a_turn_starting_on_a_work_he_just_stopped_is_stopped_at_once(self):
        """His stop reached tasks[-1] — the turn before, when the new one hadn't written itself down yet: it ran to its end."""
        wid = self.new()
        work._change(wid, lambda w: w.update(status=work.STOPPED, running=moment.now(), tasks=["turn-1"]))
        with mock.patch.object(work.cancel, "stop_task") as stop:
            work.turn_began(wid, "turn-2")
        stop.assert_called_once_with("turn-2")
        self.assertEqual(work.get(wid)["tasks"], ["turn-1", "turn-2"])
        work._change(wid, lambda w: w.update(status=work.ACTIVE))
        with mock.patch.object(work.cancel, "stop_task") as stop:
            work.turn_began(wid, "turn-3")
        stop.assert_not_called()

    def test_a_verdict_of_the_wrong_shape_is_still_booked(self):
        """The schema isn't strict: «missing» as one string was walked letter by letter, a wait in words raised inside the
        bookkeeping and the turn's checkpoint and the word to him were lost."""
        wid = self.new()
        self.verdicts = [SimpleNamespace(output_text=json.dumps({"reason": "ждём сборку", "verdict": "wait", "missing": "сайт открывается",
                                                                 "wait_minutes": "полчаса", "progress": "почти", "message": None}), output=[], id="j")]
        self.answers = ["Сделано: запустила сборку."]
        work.tick()
        w = work.get(wid)
        self.assertNotIn(wid, work._running)
        self.assertIn("запустила сборку", w["checkpoint"])   # booked
        self.assertEqual(w["missing"], ["сайт открывается"])
        self.assertGreaterEqual(w["next_at"], moment.now() + 9 * 60)   # a wait it couldn't read is the default wait, not a crash
        self.assertEqual(work._verdict({"verdict": "wait", "wait_minutes": 5000})["wait_minutes"], 1440)

    def test_the_log_he_sees_says_the_judges_word_in_russian(self):
        """«Долгие работы» showed «ход 3: continue — …»."""
        wid = self.new()
        self.verdicts = [verdict("continue", "вёрстка готова")]
        work.tick()
        text = work.get(wid)["log"][-1]["text"]
        self.assertEqual(text, "ход 1: дальше — вёрстка готова")
        self.assertEqual(set(work.VERDICTS), set(work.VERDICT["properties"]["verdict"]["enum"]))

    def test_his_press_under_a_waiting_works_question_answers_it(self):
        """The press used to reach no work: it stayed «ждёт тебя», was reminded of a day later and paused after a week."""
        wid = self.new()
        self.answers = [lambda w: work.need_owner(w["id"], "Отправить Ольге акты за октябрь?") and "Подготовила письмо Ольге."]
        self.verdicts = [verdict("need_owner", "нужно «да» на письмо")]
        work.tick()
        self.assertEqual(work.get(wid)["status"], work.WAITING)
        tid = work.get(wid)["tasks"][-1]
        self.assertIn("снова в работе", work.answered(tid, "Тема нажал «Да» — письмо olga@x: Письмо отправлено"))
        w = work.get(wid)
        self.assertEqual((w["status"], w["question"]), (work.ACTIVE, ""))
        self.verdicts = [verdict("continue")]
        work.tick()
        self.assertIn("Тема нажал «Да» — письмо olga@x", work.brief(self.turns[-1]))   # the next turn knows the mail went out
        self.assertIn("Записала", work.answered(tid, "Тема нажал «Нет» — второе письмо: Отменено"))   # still going: a note
        self.assertIn("второе письмо", work.get(wid)["notes"][-1]["text"])
        self.assertIsNone(work.answered("not-a-work-turn", "Тема нажал «Да»"))
        self.assertIsNone(work.answered(None, "Тема нажал «Да»"))

    def test_the_judge_is_told_every_field_it_answers(self):
        """pydantic-ai's order (reason before the verdict) only holds when the prompt names the fields: «verdict» wasn't named."""
        for field in work.VERDICT["properties"]:
            self.assertIn(field, work.JUDGE, field)
        self.assertIn("Ещё не поручено [id]", work.BRIEF)   # a held coding step is asked about, not reported as a failure

    def test_a_work_started_after_someone_elses_text_marks_each_of_its_turns(self):
        """A mail could have the coordinator start a work, and its turns ran the terminal and Claude Code with no «да»."""
        from sena.core import status
        status.begin("что в почте?")
        self.addCleanup(status.end, "")
        status.touched("письмо от незнакомца")
        said = work.start("Скрипт", "написать скрипт, который просит письмо", "скрипт запускается")
        self.assertIn("будут ждать «да» Темы", said)
        wid = said.split("[")[1].split("]")[0]
        status.end("")
        self.assertIsNone(status.outside())
        status.begin("Работа «Скрипт»: ход 1")
        work.turn_began(wid, status.current()["id"])
        self.assertIn("письмо от незнакомца", status.outside())
        status.end("")
        clean = self.new(title="Своя")
        status.begin("Работа «Своя»: ход 1")
        work.turn_began(clean, status.current()["id"])
        self.assertIsNone(status.outside())   # his own work starts clean

    def test_a_record_it_cant_read_never_stops_the_engine_or_the_other_works(self):
        """A record edited by hand or left by another version raised at start: app.py's guard kept Sena starting, but the engine
        stayed unwired for the whole run — and any one bad record broke every work and the context of every message."""
        from sena.core import events
        good = self.new()
        with work._lock:
            items = work._all()
            items += ["мусор", {"id": "bad1", "title": "Старая", "status": "в работе", "running": 1}, {"id": "bad2", "turns": "три"}]
            work._save(items)
        work._runner = None
        with mock.patch.object(work, "resume_after_restart", side_effect=RuntimeError("сломано")), self.assertRaises(RuntimeError):
            work.start_engine(self.runner, self.told.append)
        self.assertEqual(work._runner, self.runner)   # wired first: a failure after it leaves the engine working
        work._change(good, lambda w: w.update(running=moment.now()))   # its turn was cut by the last stop
        self.assertEqual(work.start_engine(self.runner, self.told.append), 1)
        self.assertTrue(work.get(good)["interrupted"])
        self.assertIsNone(work.get("bad1"))
        said = [r["text"] for r in events.search("all", kind="error", limit=10) if "Долгие работы при запуске" in r["text"]]
        self.assertIn("bad1", said[-1])
        self.assertIn("bad2", said[-1])
        self.verdicts = [verdict("continue")]
        self.assertEqual(work.tick(), 1)   # the good one goes on
        self.assertIn("Сайт бутылок", work.context())
        self.assertEqual([w["title"] for w in work.all_works()], ["Сайт бутылок"])   # the panel lists what it can show
        self.assertEqual(len(work._all()), 4)   # the bad ones are kept as they were, for a look by hand
        self.assertEqual(work._all()[2]["running"], 1)

    def test_no_turn_starts_while_a_restart_waits_for_the_running_tasks(self):
        from sena.core import power
        self.new()
        self.addCleanup(setattr, power, "_restart", False)
        power.request_restart()   # ag_code._restart asks for it before it waits
        self.assertEqual(work.tick(), 0)
        power._restart = False
        self.verdicts = [verdict("continue")]
        self.assertEqual(work.tick(), 1)

    def test_the_panel_shows_works_and_acts_on_them(self):
        wid = self.new()
        shown = panels.panel("works")
        [row] = shown["blocks"][1]["items"]
        self.assertEqual(row["id"], wid)
        self.assertIn("в работе", row["subtitle"])
        result = panels.act({"panel": "works", "action": "work_pause", "args": {"id": wid}})
        self.assertIn("на паузе", result["result"])
        result = panels.act({"panel": "works", "action": "work_resume", "args": {"id": wid}, "values": {"value": "можно дальше"}})
        self.assertIn("снова в работе", result["result"])
        self.assertIn({"id": "works", "title": "Долгие работы", "badge": 0}, panels.listing()["items"])


class Effects(unittest.TestCase):
    def test_a_confirmed_action_leaves_started_and_done_under_the_task_that_prepared_it(self):
        from sena.core import confirm, events, status
        status.begin("письмо Ольге")
        tid = status.current()["id"]
        status.new_turn("да")
        sid = confirm.hold("письмо olga@x", "акты за октябрь", lambda: "Отправлено")
        status.end("")
        status.new_turn("да")   # the owner's next message: his "yes"
        done, result, _ = confirm.confirm(sid)
        self.assertTrue(done)
        rows = events.search("all", kind="effect", tid=tid)
        self.assertEqual([r["state"] for r in rows], ["start", "done"])
        self.assertEqual(rows[0]["key"], rows[1]["key"])
        self.assertEqual(work._effects(tid), ["сделано: письмо olga@x: акты за октябрь"])


class Unfinished(unittest.TestCase):
    def test_an_action_cut_by_a_fall_is_named_to_the_owner_once(self):
        from sena.core import confirm, events
        events.log("effect", "сообщение «Игорю»: буду в 5", key="cutA", state="start", tid="tx")
        events.log("effect", "письмо olga@x: акты", key="okB", state="start", tid="tx")
        events.log("effect", "письмо olga@x: отправлено", key="okB", state="done", tid="tx")
        left = dict(confirm.unfinished())
        self.assertIn("cutA", left)
        self.assertNotIn("okB", left)
        confirm.told(["cutA"])
        self.assertNotIn("cutA", dict(confirm.unfinished()))   # told once: not again after the next restart

    def test_an_action_that_failed_is_a_failure_not_an_interruption(self):
        """n8n's study: a confirmed send that raised kept only its "started" line, and after the next start the owner heard it
        was cut by a shutdown; a declared failure is the same."""
        from sena.core import confirm
        from sena.core.tools import Failure
        boom = confirm.hold("сообщение «Игорю»", "буду в 5", lambda: (_ for _ in ()).throw(ConnectionError("нет сети")))
        with self.assertRaises(ConnectionError):
            confirm.confirm_now(boom)
        refused = confirm.hold("письмо ivan@x", "акты", lambda: Failure("адрес не существует"))
        self.assertIsInstance(confirm.confirm_now(refused), Failure)
        left = [text for _, text in confirm.unfinished()]
        self.assertFalse(any("Игорю" in t or "ivan@x" in t for t in left))

    def test_an_action_that_surely_went_nowhere_waits_for_another_yes(self):
        """Flowise's check on this Mac: a refused connection on the first press removed the prepared mail, and the second press
        said "there is no such action any more". What surely didn't go out waits under its id; what may have gone doesn't."""
        from sena.core import confirm
        from sena.core.tools import Failure
        tries = []

        def send():
            tries.append(1)
            if len(tries) == 1:
                raise ConnectionRefusedError("[Errno 61] Connection refused")
            return "Отправлено"
        sid = confirm.hold("письмо ivan@x", "акты", send)
        first = confirm.confirm_now(sid)
        self.assertIn("Ничего не ушло", first)
        self.assertIn(sid, confirm.pending)
        self.assertEqual(confirm.confirm_now(sid), "Отправлено")   # the second «Да» repeats it
        self.assertNotIn(sid, confirm.pending)
        slow = confirm.hold("сообщение «Игорю»", "буду в 5", lambda: (_ for _ in ()).throw(TimeoutError("Telegram не ответил за 60 с")))
        with self.assertRaises(TimeoutError):
            confirm.confirm_now(slow)
        self.assertNotIn(slow, confirm.pending)   # it may have reached him: never offered blindly again
        busy = confirm.hold("сообщение «Олегу»", "привет", lambda: Failure("A wait of 30 seconds is required (FloodWaitError)"))
        for _ in range(confirm.TRIES - 1):
            self.assertIn("Ничего не ушло", confirm.confirm_now(busy))
        self.assertNotIn("Ничего не ушло", confirm.confirm_now(busy))   # not forever
        self.assertNotIn(busy, confirm.pending)

    def test_a_failed_answer_says_what_already_went_out(self):
        from sena.agents.ag_sena import agent
        from sena.core import confirm

        def run(*a, **kw):
            sid = confirm.hold("сообщение «Маме»", "задержусь", lambda: "Отправлено")
            p = confirm.pending.pop(sid)
            confirm._run(p)   # it went out (as if confirmed earlier in this task), then the cloud fell
            raise ConnectionError("connection reset")
        with mock.patch.object(agent.brain, "run", run), mock.patch.object(agent, "without_cloud", lambda *a: None), \
             mock.patch.object(agent.ag_judge, "after_task"):
            answer = agent.ask(config.OWNER_ID, "напиши маме, что задержусь, и закажи такси")
        self.assertIn("Не получилось ответить", answer)
        self.assertIn("До сбоя: сделано: сообщение «Маме»: задержусь", answer)


class WorkTurn(unittest.TestCase):
    def test_a_turn_is_the_coordinator_with_its_work_tools_at_hand_and_sends_nothing(self):
        from sena.agents.ag_sena import agent
        store.clear(config.WORKS_FILE)
        wid = work.start("Книга", "прочитать книгу и выписать полезное мне", "в заметке есть выписки по всем главам").split("[")[1].split("]")[0]
        seen = {}

        def fake_run(model, effort, instructions, turn, tools, functions, **kw):
            seen["tools"] = {t["name"]: t.get("defer", False) for t in tools if t.get("type") == "function"}
            seen["context"] = turn[-1]["content"]
            return "Сделано: глава 1."
        with mock.patch.object(agent.brain, "run", fake_run), mock.patch.object(agent.api, "send_text") as sent, \
             mock.patch.object(agent.ag_judge, "after_task"):
            answer, tid, outcome = agent.work_turn({**work.get(wid), "turns": 1})
        self.assertEqual((answer, outcome), ("Сделано: глава 1.", "ok"))
        self.assertIn(tid, work.get(wid)["tasks"])   # written at the start of the turn, not after it
        self.assertFalse(seen["tools"]["work_done"] or seen["tools"]["work_need"] or seen["tools"]["work_plan"])
        self.assertTrue(seen["tools"]["work_start"])   # starting another work stays in its group
        self.assertIn("ход 1 из 12 долгой работы", seen["context"])
        sent.assert_not_called()

    def test_a_turn_of_a_work_started_after_foreign_text_is_marked_before_its_model_runs(self):
        """The laundering the audit proved: a mail had the coordinator start a work, and the work's turns ran Claude Code and the
        terminal with no «да» — the mark stayed with the task that read the mail. The real turn now starts marked."""
        from sena.agents import ag_code
        from sena.agents.ag_sena import agent
        from sena.core import confirm, status
        store.clear(config.WORKS_FILE)
        status.begin("что в почте?")
        status.touched("письмо от незнакомца")
        wid = work.start("Скрипт", "написать скрипт, о котором просит письмо", "скрипт запускается").split("[")[1].split("]")[0]
        status.end("")
        seen = {}

        def fake_run(model, effort, instructions, turn, tools, functions, **kw):
            seen["outside"] = status.outside()
            seen["held"] = functions["claude_task"]("KSclay", "сверстай главную")
            return "Сделано: спросила Тему."
        with mock.patch.object(agent.brain, "run", fake_run), mock.patch.object(agent.api, "send_text"), \
             mock.patch.object(agent.ag_judge, "after_task"), mock.patch.object(ag_code, "run") as code:
            agent.work_turn({**work.get(wid), "turns": 1})
        self.assertIn("письмо от незнакомца", seen["outside"])
        self.assertIn("Ещё не поручено", seen["held"])   # Claude Code waits for his «да»
        code.assert_not_called()
        confirm.pending.pop(seen["held"].split("[")[1].split("]")[0], None)


class FakeLuma:
    """Luma's project and card tools as the work sync uses them, behind the one HTTP call (luma._post): the real luma.call
    runs on top, with its rule that a batch of only refusals raises — the fake used to stand in for call() itself and hid
    that a deleted card switched the whole sync off. Ids for what is made; a deleted card, or a title Luma won't take, is
    a refused item of a batch that still answers 200, as Luma's runBatch does."""
    def __init__(self):
        self.calls, self.up, self.fail, self.gone, self.bad, self.made = [], True, None, set(), set(), 0

    def post(self, tool, input):
        if self.fail:
            raise work.luma.LumaError(self.fail)
        self.calls.append((tool, input))
        items = input["items"]
        if tool == "project_add":
            return {"created": [{"id": "p1", "title": items[0]["title"], "status": items[0]["status"]}], "failed": []}, False
        if tool == "task_add":
            created, failed = [], []
            for i, item in enumerate(items):
                if item["title"] in self.bad:
                    failed.append({"index": i, "item": item, "error": "Invalid input: title"})
                    continue
                self.made += 1
                created.append({"index": i, "id": f"c{self.made}", "title": item["title"], "status": "todo"})
            return {"created": created, "failed": failed}, False
        failed = [{"index": i, "item": item, "error": "No tasks match"} for i, item in enumerate(items) if item.get("task") in self.gone]
        return {"updated": [item for item in items if item.get("task") not in self.gone], "failed": failed}, False


class LumaProject(unittest.TestCase):
    """A work for one of the owner's projects keeps it in Luma: the project's status follows the work, each plan step is a card."""
    def setUp(self):
        store.clear(config.WORKS_FILE)
        work._running.clear()
        self.told, self.luma = [], FakeLuma()
        work.start_engine(None, self.told.append)
        for patch in (mock.patch.object(work.luma, "_post", self.luma.post), mock.patch.object(work.luma, "is_up", lambda: self.luma.up)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(work.start_engine, None, None)
        self.wid = work.start("Лендинг", "сделать лендинг для KSclay по требованиям", "лендинг открывается по адресу",
                              plan=["требования", "вёрстка", "выкладка"], project="KSclay лендинг").split("[")[1].split("]")[0]

    def sent(self, tool):
        return [input["items"] for name, input in self.luma.calls if name == tool]

    def test_the_project_and_its_cards_are_made_once_then_only_changes_go(self):
        work.luma_sync(self.wid)
        self.assertEqual(self.sent("project_add")[0][0]["title"], "KSclay лендинг")
        self.assertEqual(self.sent("project_add")[0][0]["status"], "active")
        self.assertEqual([i["title"] for i in self.sent("task_add")[0]], ["требования", "вёрстка", "выкладка"])
        self.assertEqual({i["project"] for i in self.sent("task_add")[0]}, {"p1"})
        self.assertFalse(work._luma_due(work.get(self.wid)))   # nothing changed: nothing more to send
        self.luma.calls.clear()
        self.assertIsNone(work.luma_sync(self.wid))
        self.assertEqual(self.luma.calls, [])
        work.set_plan(self.wid, [{"text": "требования", "status": "done"}, {"text": "вёрстка", "status": "doing"}, {"text": "тексты", "status": "todo"}])
        work.luma_sync(self.wid)
        self.assertEqual(self.sent("task_add"), [[{"title": "тексты", "project": "p1"}]])
        self.assertEqual(sorted((i["task"], i["status"]) for i in self.sent("task_update")[0]),
                         [("c1", "done"), ("c2", "doing"), ("c3", "cancelled")])   # a dropped step's card is cancelled, not deleted
        self.assertNotIn("project_add", [name for name, _ in self.luma.calls])

    def test_the_projects_status_follows_the_work(self):
        work.luma_sync(self.wid)
        for action, status in (("pause", "on_hold"), ("resume", "active"), ("stop", "dropped")):
            self.luma.calls.clear()
            work.control(self.wid, action)
            work.luma_sync(self.wid)
            self.assertEqual(self.sent("project_update"), [[{"project": "p1", "status": status}]])

    def test_a_closed_luma_is_waited_for_and_a_deleted_card_left_alone(self):
        self.luma.up = False
        work.luma_sync(self.wid)
        self.assertEqual(self.luma.calls, [])   # never opened from the background
        w = work.get(self.wid)
        self.assertEqual(w["luma"]["fails"], 0)
        self.assertFalse(work._luma_due(w))   # tried again later
        work._change(self.wid, lambda w: w["luma"].update(next_try=0))
        self.luma.up = True
        work.luma_sync(self.wid)
        self.luma.gone.add("c1")   # the owner deleted the first card in Luma
        work.set_plan(self.wid, [{"text": "требования", "status": "done"}, {"text": "вёрстка", "status": "todo"}, {"text": "выкладка", "status": "todo"}])
        work.luma_sync(self.wid)   # the only move is that card's: a batch Luma refused whole
        p = work.get(self.wid)["luma"]
        self.assertIsNone(p["cards"]["требования"])
        self.assertEqual((p["fails"], p.get("off")), (0, None))   # his deletion is no failure: the sync stays on
        self.assertFalse(work._luma_due(work.get(self.wid)))   # not sent again and again
        self.luma.calls.clear()
        work.set_plan(self.wid, [{"text": "требования", "status": "done"}, {"text": "вёрстка", "status": "done"}, {"text": "выкладка", "status": "doing"}])
        work.luma_sync(self.wid)
        self.assertEqual(sorted((i["task"], i["status"]) for i in self.sent("task_update")[0]), [("c2", "done"), ("c3", "doing")])   # the rest goes on
        self.assertEqual(self.told, [])

    def test_a_step_luma_refuses_is_left_out_with_its_reason_in_the_journal(self):
        from sena.core import events
        self.luma.bad.add("выкладка")
        work.luma_sync(self.wid)
        p = work.get(self.wid)["luma"]
        self.assertEqual((p["cards"]["выкладка"], p["fails"]), (None, 0))
        self.assertTrue(any("выкладка" in r["text"] and "Invalid input" in r["text"] for r in events.search("all", kind="work", limit=10)))
        work.set_plan(self.wid, [{"text": "выкладка", "status": "done"}, {"text": "требования", "status": "done"}, {"text": "вёрстка", "status": "todo"}])
        self.luma.calls.clear()
        work.luma_sync(self.wid)
        self.assertEqual(self.sent("task_add"), [])   # not tried again
        self.luma.bad.add("тексты")
        work.set_plan(self.wid, [{"text": "выкладка", "status": "done"}, {"text": "требования", "status": "done"}, {"text": "тексты", "status": "todo"}])
        work.luma_sync(self.wid)   # a new step refused alone: the batch refused whole doesn't count as a failed sync
        self.assertEqual(work.get(self.wid)["luma"]["fails"], 0)

    def test_the_owners_luma_switch_holds_the_sync_until_he_turns_it_on(self):
        from sena.core import settings
        before = settings.get("use_luma")
        self.addCleanup(settings.update, {"use_luma": before})
        settings.update({"use_luma": False})
        self.assertIsNone(work.luma_sync(self.wid))
        self.assertEqual(self.luma.calls, [])   # nothing written to his app while he has it switched off
        self.assertIn("пока Luma выключена", work.listing())
        said = work.start("Каталог", "сверстать каталог для KSclay по образцу", "каталог открывается", project="KSclay каталог")
        self.assertIn("сейчас она выключена", said)
        settings.update({"use_luma": True})
        self.assertTrue(work._luma_due(work.get(self.wid)))   # it catches up
        work.luma_sync(self.wid)
        self.assertEqual(self.sent("project_add")[0][0]["title"], "KSclay лендинг")

    def test_a_project_given_up_is_not_listed_as_kept(self):
        work._change(self.wid, lambda w: w["luma"].update(off=True))
        shown = work.listing()
        self.assertNotIn("проект в Luma «KSclay лендинг»", shown)
        self.assertIn("больше не веду", shown)

    def test_luma_refusing_again_and_again_ends_with_one_word_to_the_owner(self):
        self.luma.fail = "Luma отказала (validation): плохое поле"
        for _ in range(work.LUMA_FAILS):
            work._change(self.wid, lambda w: w["luma"].update(next_try=0))
            work.luma_sync(self.wid)
        w = work.get(self.wid)
        self.assertTrue(w["luma"]["off"])
        self.assertEqual(len(self.told), 1)
        self.assertIn("без неё", self.told[0])
        self.assertFalse(work._luma_due(w))

    def test_a_work_without_a_project_never_touches_luma(self):
        wid = work.start("Почта", "разобрать всю почту по папкам", "во входящих меньше 50 писем").split("[")[1].split("]")[0]
        self.assertNotIn("luma", work.get(wid))
        self.assertIsNone(work.luma_sync(wid))


if __name__ == "__main__":
    unittest.main()
