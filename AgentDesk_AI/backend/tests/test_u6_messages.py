"""U6: chat messages (WhatsApp / Telegram / Teams): read-back, "yes, send it", pressing Send through UI Automation, and looking at the chat afterwards.

The apps are a fake desktop (tests/helpers/fake_uia.py): a chat window whose composer takes the text from the deep link, whose Send button
posts it to the conversation, and which can be made to misbehave. Nothing real is opened or sent.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.mail import service as mail_service
from app.mail.models import Contact
from app.models import MemoryItem, TaskStatus
from app.orchestration import executor, message_flow
from app.preferences import PreferencesUpdate, update_preferences
from app.tools import messenger
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry
from app.tools.uia import UiaService, set_service
from tests.helpers.fake_mail import FakeMail
from tests.helpers.fake_uia import FC, FakeBackend, FakeWorld

SETTINGS = Settings(ai_provider="groq", groq_api_key=None)


class Chat:
    """One chat window: opening a deep link fills the composer; Send posts it."""

    def __init__(self, title="WhatsApp", send_works=True, shows_message=True):
        self.world = FakeWorld()
        self.opened: list[str] = []
        self.clicks = 0
        self.send_works, self.shows_message = send_works, shows_message
        self.title = title

    def open_link(self, uri: str) -> None:
        self.opened.append(uri)
        text = parse_qs(urlparse(uri).query).get("text", parse_qs(urlparse(uri).query).get("message", [""]))[0]

        def send(wld, win, fc):
            self.clicks += 1
            if not self.send_works:
                return
            composer = win.get("Type a message")
            if self.shows_message:
                win.controls.append(FC(composer.value, "Text", composer.value))
            composer.value = ""
        self.world.windows.clear()
        controls = [FC("Type a message", "Edit", text), FC("Send", on_click=send), FC("Ahmed", "Text", "Ahmed")]
        if not self.send_works and False:
            controls = controls[:1]
        self.world.add(self.title, "whatsapp.exe", controls)
        self.world.windows[-1].get("Type a message")


@pytest.fixture()
def chat(db, tmp_path, monkeypatch):
    c = Chat()
    set_service(UiaService(FakeBackend(c.world)))
    monkeypatch.setattr(executor, "get_provider", lambda *_: pytest.fail("cloud used"))
    monkeypatch.setattr("app.tools.messenger._platform.open_path_with_os", c.open_link)
    monkeypatch.setattr("app.tools.messenger.time.sleep", lambda s: None)
    db.add(MemoryItem(kind="fact", text="Ahmed: +923001234567"))
    db.commit()
    registry = ToolRegistry(ToolContext(allowed_directories=[tmp_path]))
    yield c, registry
    set_service(None)
    messenger.save(db, None)
    mail_service.set_provider_override(None)
    update_preferences(db, PreferencesUpdate(mail_provider="none"))


def say(db, registry, text):
    task = executor.start_task(db, text, None, registry, SETTINGS)
    db.refresh(task)
    return task


def test_the_spec_example_end_to_end(db, chat):
    c, registry = chat
    t = say(db, registry, "In WhatsApp, message Ahmed 'I'm on my way'")
    assert t.plan_note == "To Ahmed on WhatsApp: I'm on my way. Shall I send it?"
    assert c.opened == [] and c.clicks == 0                       # nothing opened, nothing sent before the yes
    done = say(db, registry, "yes, send it")
    assert done.status == TaskStatus.COMPLETED and done.actions[0].tool_name == "send_message"
    assert c.opened == ["whatsapp://send?phone=923001234567&text=I%27m%20on%20my%20way"] and c.clicks == 1
    ver = json.loads(done.actions[0].verification_json)
    assert ver["passed"] is True and ver["evidence"]["chat_shows_it"] is True
    from app.voice import responses
    assert responses.line_for_task(executor.task_out(done).model_dump(mode="json"), "en") == "Message sent to Ahmed."
    assert messenger.load(db) is None


def test_the_other_ways_to_say_it(db, chat):
    c, registry = chat
    for text in ("message Ahmed I'm late", "send a whatsapp to Ahmed saying I will be late", "tell Ahmed on WhatsApp that I am late", "whatsapp Ahmed see you soon",
                 "Ahmed ko whatsapp karo ke main aa raha hoon"):
        t = say(db, registry, text)
        assert "To Ahmed on WhatsApp:" in t.plan_note, text
        say(db, registry, "no")
        assert messenger.load(db) is None


def test_an_unknown_number_is_asked_for_and_checked(db, chat):
    c, registry = chat
    t = say(db, registry, "message Sara hello how are you")
    assert t.plan_note == "What is Sara's number, with the country code?"
    assert say(db, registry, "0300 1234567").plan_note.startswith("That is not a valid number")
    t = say(db, registry, "plus 92 300 7654321")
    assert t.plan_note.startswith("To Sara on WhatsApp: hello how are you") and messenger.load(db).target == "923007654321"


def test_only_a_yes_that_says_send_sends(db, chat):
    c, registry = chat
    say(db, registry, "message Ahmed on my way now")
    for reply in ("yes", "okay", "sure", "yes please", "I will send it later", "send it to Sara", "go"):
        say(db, registry, reply)
        assert c.opened == [] and c.clicks == 0, reply
        assert messenger.load(db) is not None
    assert say(db, registry, "no").plan_note == "Okay, I won't send it." and messenger.load(db) is None


def test_changing_the_text_voids_the_confirmation_and_reads_it_back_again(db, chat):
    c, registry = chat
    say(db, registry, "message Ahmed on my way now")
    old = messenger.load(db).confirm["token"]
    t = say(db, registry, "change it to I will be ten minutes late")
    assert "ten minutes late" in t.plan_note and t.plan_note.endswith("Shall I send it?")
    d = messenger.load(db)
    assert d.confirm["token"] != old and not d.confirmation_valid(old)
    say(db, registry, "yes send it")
    assert c.opened and "ten%20minutes%20late" in c.opened[0]


def test_a_stale_confirmation_is_read_back_again(db, chat):
    c, registry = chat
    say(db, registry, "message Ahmed on my way now")
    d = messenger.load(db)
    d.confirm["at"] -= messenger.CONFIRM_SECONDS + 5
    messenger.save(db, d)
    t = say(db, registry, "yes send it")
    assert c.opened == [] and t.plan_note.endswith("Shall I send it?")


def test_the_model_cannot_plan_it_and_a_made_up_confirmation_sends_nothing(db, chat):
    from app.ai.base import Plan, PlanAction
    c, registry = chat
    assert "send_message" in registry.names() and "send_message" not in registry.model_names()
    say(db, registry, "message Ahmed on my way now")
    task = executor.create_task(db, "send it")
    task = executor.plan_task(db, task, registry, SETTINGS, preplanned=Plan(actions=[PlanAction(tool="send_message", args={"app": "whatsapp", "confirmation": "guess-guess-guess"})]))
    executor.approve_task(db, task.id, [a.id for a in task.actions])
    done = executor.run_task(db, task.id, registry, SETTINGS)
    assert done.status == TaskStatus.FAILED and c.opened == [] and c.clicks == 0


def test_a_missing_send_button_is_reported_and_nothing_is_claimed(db, chat):
    c, registry = chat
    original = c.open_link

    def no_button(uri):
        original(uri)
        c.world.windows[-1].controls[:] = [x for x in c.world.windows[-1].controls if x.name != "Send"]
    import app.tools.messenger as m
    m._platform.open_path_with_os = no_button
    import time
    real_monotonic = time.monotonic
    ticks = iter(range(0, 100000, 3))
    m.time.monotonic = lambda: next(ticks)
    try:
        say(db, registry, "message Ahmed on my way now")
        done = say(db, registry, "yes send it")
    finally:
        m.time.monotonic = real_monotonic
    assert done.status == TaskStatus.FAILED and "could not press Send" in (done.error or "") and "Press Send yourself" in done.error
    assert c.clicks == 0


def test_a_message_that_does_not_show_up_in_the_chat_is_not_called_sent(db, chat):
    c, registry = chat
    c.shows_message = False
    say(db, registry, "message Ahmed on my way now")
    done = say(db, registry, "yes send it")
    assert c.clicks == 1 and done.status != TaskStatus.COMPLETED
    from app.voice import responses
    line = responses.line_for_task(executor.task_out(done).model_dump(mode="json"), "en")
    assert "Message sent" not in line
    assert messenger.load_any(db).sent                                      # a retry can never press Send a second time


def test_a_second_send_is_refused_after_the_button_was_pressed(db, chat):
    c, registry = chat
    say(db, registry, "message Ahmed on my way now")
    token = messenger.load(db).confirm["token"]
    say(db, registry, "yes send it")
    assert c.clicks == 1
    tool = registry.get("send_message")
    again = tool.execute(tool.validate_args({"app": "whatsapp", "confirmation": token}))
    assert not again.success and c.clicks == 1


def test_telegram_and_teams_use_their_own_links_and_recipients(db, chat):
    c, registry = chat
    db.add(MemoryItem(kind="fact", text="Sara: @sara_k"))
    db.commit()
    c.title = "Telegram"
    t = say(db, registry, "telegram message Sara see you at five")
    assert "To Sara on Telegram: see you at five" in t.plan_note
    say(db, registry, "yes send it")
    assert c.opened[-1].startswith("tg://msg?text=see%20you%20at%20five&to=@sara_k")
    # Teams: the recipient is a contact of the connected e-mail account
    fake = FakeMail([Contact("Bilal Shah", "bilal@corp.example")])
    mail_service.set_provider_override(fake)
    update_preferences(db, PreferencesUpdate(mail_provider="gmail"))
    c.title = "Microsoft Teams"
    t = say(db, registry, "message Bilal on Teams: meeting at 5")
    assert "To Bilal Shah on Microsoft Teams: meeting at 5" in t.plan_note
    say(db, registry, "yes send it")
    assert c.opened[-1] == "msteams:/l/chat/0/0?users=bilal@corp.example&message=meeting%20at%205"


def test_ordinary_commands_are_not_taken_for_messages(db, chat):
    _, registry = chat
    for text in ("text size bigger", "open chrome", "show trash", "what is the message of the story"):
        assert message_flow.parse_message(text) is None, text
    t = say(db, registry, "show trash")
    assert t.actions and t.actions[0].tool_name == "list_trash"


def test_deep_links():
    assert messenger.deep_link("whatsapp", "923001234567", "a b&c") == "whatsapp://send?phone=923001234567&text=a%20b%26c"
    assert messenger.deep_link("telegram", "923001234567", "hi") == "tg://msg?text=hi&to=+923001234567"
    assert messenger.deep_link("teams", "a@b.co", "hi there") == "msteams:/l/chat/0/0?users=a@b.co&message=hi%20there"
