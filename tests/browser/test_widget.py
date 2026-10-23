"""End-to-end widget tests in a real browser (backlog 54, 55, 56, 57, 78).

The real widget.js runs in Chromium (and WebKit, the engine behind Safari
on iPhone) against the real FastAPI app on a local port, with in-memory
stores and a scripted model, so nothing reaches a paid API. Pages are
served from http://localhost:<port> and the widget and API from
http://127.0.0.1:<port>, so every request is cross-origin, as on a
customer's site (a same-origin GET carries no Origin header, which would
skip the domain check).

Skipped when Playwright or its browsers are not installed (as in CI). To
run them locally:

    pip install playwright
    python -m playwright install chromium webkit
    pytest tests/browser
"""

import json
import re
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import SecretStr  # noqa: E402

import app.api.dependencies as deps  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.conversations.store import InMemoryConversationStore  # noqa: E402
from app.documents.store import InMemoryChunkStore  # noqa: E402
from app.facts.store import InMemoryFactStore  # noqa: E402
from app.leads.store import InMemoryLeadStore  # noqa: E402
from app.main import create_app  # noqa: E402
from app.programmes.store import InMemoryProgrammeStore  # noqa: E402
from app.usage.store import InMemoryUsageStore  # noqa: E402
from app.feedback.store import InMemoryFeedbackStore  # noqa: E402
from app.rate_limit import limiter  # noqa: E402
from app.tenants.models import Tenant  # noqa: E402
from app.tenants.store import InMemoryTenantStore  # noqa: E402
from tests.test_api_security import NoOpVectorStore  # noqa: E402

expect = sync_api.expect

# What the scripted model says, by a word in the visitor's message.
REPLIES = {
    "paragraphs": "The 200-hour course is US $1,199.\n\nThat includes meals and a room.",
    "included": "Here's what's included:\n\n- Meals\n- A room\n\n- A yoga kit",
    "founder": "Our founder is **Ravi Nagar**.",
    "inject": '<img src=x onerror="window.__pwned = 1"> <b>bold?</b>',
}
CONTACT_MESSAGE = "I'd like someone from the team to contact me."

# The kind of CSS a busy theme carries. None of it should reach the widget.
HOSTILE_CSS = """
  * { line-height: 3 !important; letter-spacing: 4px !important; }
  button { background: rgb(255, 0, 0) !important; font-size: 40px !important;
           border: 5px solid lime !important; }
  p { color: rgb(255, 0, 0) !important; }
  textarea { display: none !important; }
"""


def scripted_model(messages, tools, **_):
    message = MagicMock()
    message.tool_calls = None
    last = messages[-1]

    # After contact_team, answer the way the tool result says to.
    if last["role"] == "tool":
        message.content = (
            "Could you share your email address?"
            if "Ask them for it" in last["content"]
            else "Someone from the team will be in touch."
        )
        return message

    text = last["content"].lower()
    if "contact me" in text:  # the Talk to the team button's message
        call = MagicMock()
        call.id = "call_1"
        call.function.name = "contact_team"
        call.function.arguments = '{"reason": "wants the team to get in touch"}'
        call.model_dump.return_value = {
            "id": "call_1", "type": "function",
            "function": {"name": "contact_team", "arguments": call.function.arguments},
        }
        message.content = None
        message.tool_calls = [call]
        return message

    message.content = next(
        (reply for word, reply in REPLIES.items() if word in text), "Fine."
    )
    return message


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    tenants = InMemoryTenantStore()
    tenants.save(Tenant(
        tenant_id="t1", site_key="pk_local", display_name="Pyramid Yogshala",
        allowed_domains=["localhost"], greeting="Namaste! Ask me anything.",
        team_email="team@example.com", reply_time="within 24 hours",
        suggested_questions=["What does it cost?", "Who is the founder?"],
    ))
    tenants.save(Tenant(
        tenant_id="t2", site_key="pk_elsewhere", display_name="Other",
        allowed_domains=["example.com"],
    ))
    app = create_app(
        tenant_store=tenants,
        conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(),
        vector_store=NoOpVectorStore(),
        chunk_store=InMemoryChunkStore(),
        lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )

    port = free_port()

    @app.get("/host", response_class=HTMLResponse)
    def host_page(request: Request, key: str = "pk_local", hostile: bool = False):
        style = f"<style>{HOSTILE_CSS}</style>" if hostile else ""
        # The page fixture's cookie: the site turns the teaser off.
        teaser = ' data-teaser="off"' if request.cookies.get("teaser") == "off" else ""
        return (
            "<!doctype html><html><head>"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"{style}</head><body>"
            "<h1>A customer's page</h1><p>Some text.</p>"
            f'<script src="http://127.0.0.1:{port}/widget/widget.js" '
            f'data-site-key="{key}"{teaser}></script>'
            "</body></html>"
        )

    settings = get_settings()
    saved = (settings.rate_limit_per_visitor, settings.rate_limit_per_tenant,
             settings.rate_limit_config_per_visitor, settings.resend_api_key)
    # Lead email "set up", with sending patched below, so contact requests
    # are confirmed in the chat as in production.
    settings.resend_api_key = SecretStr("re_test")
    # Every test comes from 127.0.0.1; the limits are tested in
    # test_api_security, here they would only get in the way.
    settings.rate_limit_per_visitor = "1000/minute"
    settings.rate_limit_per_tenant = "1000/hour"
    settings.rate_limit_config_per_visitor = "1000/minute"
    deps._cache.clear()
    limiter.reset()

    patches = [
        patch("app.agent.orchestrator.complete_with_tools", side_effect=scripted_model),
        patch("app.agent.orchestrator.complete", return_value="Summary."),
        patch("app.agent.orchestrator.send_lead_email", return_value=True),
        patch("app.retrieval.pipeline.embed_query", return_value=[0.0] * 1536),
    ]
    for p in patches:
        p.start()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not uv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.05)

    yield f"http://localhost:{port}"

    uv.should_exit = True
    thread.join(timeout=5)
    for p in patches:
        p.stop()
    (settings.rate_limit_per_visitor, settings.rate_limit_per_tenant,
     settings.rate_limit_config_per_visitor, settings.resend_api_key) = saved


@pytest.fixture(scope="module")
def playwright():
    with sync_api.sync_playwright() as pw:
        yield pw


def launch(playwright, engine="chromium"):
    try:
        return getattr(playwright, engine).launch()
    except Exception as exc:  # browsers not downloaded
        pytest.skip(f"{engine} is not installed: {exc}")


@pytest.fixture(scope="module")
def browser(playwright):
    browser = launch(playwright)
    yield browser
    browser.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context()
    # The launcher teaser would change what a click on the launcher does
    # after 3 seconds; it has its own tests (teaser_page). The cookie makes
    # the host page turn it off (data-teaser="off").
    context.add_cookies([{"name": "teaser", "value": "off", "url": server}])
    page = context.new_page()
    page.base = server
    yield page
    context.close()


@pytest.fixture
def teaser_page(browser, server):
    """A first visit, with a fake clock to step through the teaser."""
    context = browser.new_context(viewport={"width": 1200, "height": 800})
    page = context.new_page()
    page.base = server
    freeze_clock(page)
    yield page
    context.close()


def freeze_clock(page):
    """Timers only move when the test says so (step)."""
    page.clock.install(time=0)
    page.clock.pause_at(1000)


def load(page, path="/host"):
    """Open the page and wait for the widget's settings, which start the teaser."""
    page.goto(page.base + path)
    expect(page.locator(".launcher")).to_have_attribute("aria-label", re.compile("^Chat with"))


def open_chat(page, path="/host"):
    page.goto(page.base + path)
    page.locator(".launcher").click()
    expect(page.locator(".turn.them").first).to_contain_text("Namaste")


def ask(page, text):
    page.locator("textarea").fill(text)
    page.locator("textarea").press("Enter")


def their_bubbles(page):
    """The bot's bubbles after the greeting."""
    return page.locator(".turn.them:not([data-waiting]) .bubble").filter(
        has_not_text="Namaste"
    )


# --- first impression -----------------------------------------------------------


def test_opens_with_greeting_suggestions_and_ai_notice(page):
    open_chat(page)
    expect(page.locator(".name")).to_have_text("Pyramid Yogshala")
    expect(page.locator(".prompts button")).to_have_text(
        ["What does it cost?", "Who is the founder?"]
    )
    expect(page.locator(".disclosure")).to_contain_text("AI assistant")
    expect(page.locator(".handoff")).to_be_hidden()


def test_suggested_question_is_asked_on_click(page):
    open_chat(page)
    page.locator(".prompts button", has_text="Who is the founder?").click()
    expect(page.locator(".turn.you .bubble")).to_have_text("Who is the founder?")
    expect(page.locator(".prompts")).to_have_count(0)
    expect(their_bubbles(page)).to_have_text(["Our founder is Ravi Nagar."])


# --- reply bubbles --------------------------------------------------------------


def test_reply_arrives_as_bubbles_one_after_another(page):
    open_chat(page)
    ask(page, "two paragraphs please")
    bubbles = their_bubbles(page)
    expect(bubbles).to_have_count(1)
    expect(page.locator(".waiting")).to_be_visible()  # typing dots before the next
    expect(bubbles).to_have_count(2)
    expect(bubbles).to_have_text([
        "The 200-hour course is US $1,199.", "That includes meals and a room.",
    ])
    expect(page.locator(".turn.them.follow")).to_have_count(1)
    expect(page.locator(".waiting")).to_have_count(0)


def test_a_list_stays_with_its_lead_in(page):
    open_chat(page)
    ask(page, "what is included?")
    expect(their_bubbles(page)).to_have_text(
        ["Here's what's included:\n- Meals\n- A room\n- A yoga kit"]
    )


def test_typing_shows_the_remaining_bubbles_at_once(page):
    open_chat(page)
    ask(page, "two paragraphs please")
    expect(their_bubbles(page)).to_have_count(1)
    page.locator("textarea").type("a")
    expect(their_bubbles(page)).to_have_count(2, timeout=300)


def test_reduced_motion_shows_all_bubbles_at_once(browser, server):
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    page.base = server
    open_chat(page)
    ask(page, "two paragraphs please")
    expect(their_bubbles(page)).to_have_count(2, timeout=2000)
    expect(page.locator(".waiting")).to_have_count(0)
    context.close()


# --- safe rendering -------------------------------------------------------------


def test_bold_is_rendered_and_html_is_not(page):
    open_chat(page)
    ask(page, "who is the founder?")
    expect(page.locator(".turn.them strong")).to_have_text("Ravi Nagar")

    ask(page, "inject something")
    expect(their_bubbles(page).last).to_have_text(
        '<img src=x onerror="window.__pwned = 1"> <b>bold?</b>'
    )
    assert page.locator(".turn.them img, .turn.them b").count() == 0
    assert page.evaluate("window.__pwned") is None


# --- talk to the team -----------------------------------------------------------


def test_talk_to_the_team_appears_after_the_first_reply_and_sends_a_request(page):
    open_chat(page)
    ask(page, "hello")
    button = page.locator(".handoff")
    expect(button).to_be_visible()
    expect(button).to_have_text("Talk to the team")

    button.click()
    expect(page.locator(".turn.you .bubble").last).to_have_text(CONTACT_MESSAGE)
    expect(their_bubbles(page).last).to_have_text("Could you share your email address?")
    expect(button).to_be_hidden()

    # A later reply does not bring it back in this chat...
    ask(page, "thanks")
    expect(their_bubbles(page).last).to_have_text("Fine.")
    expect(button).to_be_hidden()

    # ...a new chat does, after its first reply.
    page.locator(".again").click()
    expect(button).to_be_hidden()
    ask(page, "hello again")
    expect(button).to_be_visible()


# --- conversations --------------------------------------------------------------


def chat_bodies(page):
    bodies = []
    page.on("request", lambda r: r.url.endswith("/chat") and bodies.append(r.post_data_json))
    return bodies


def test_conversation_resumes_after_a_reload(page):
    bodies = chat_bodies(page)
    open_chat(page)
    ask(page, "hello")
    expect(their_bubbles(page)).to_have_count(1)

    page.reload()
    page.locator(".launcher").click()
    ask(page, "still there?")
    expect(their_bubbles(page)).to_have_count(1)

    assert "conversation_id" not in bodies[0]
    assert bodies[1]["conversation_id"]  # the id the server issued, sent back


def test_new_chat_forgets_the_conversation(page):
    bodies = chat_bodies(page)
    open_chat(page)
    ask(page, "hello")
    expect(their_bubbles(page)).to_have_count(1)

    page.locator(".again").click()
    ask(page, "hello")
    expect(their_bubbles(page)).to_have_count(1)
    assert "conversation_id" not in bodies[1]


# --- failures the visitor sees --------------------------------------------------


@pytest.mark.parametrize("status, notice", [
    (429, "That is a lot of questions at once"),
    (500, "That message did not get through"),
    (503, "That message did not get through"),
])
def test_server_errors_show_a_notice_and_the_chat_carries_on(page, status, notice):
    open_chat(page)
    page.route("**/chat", lambda route: route.fulfill(status=status, body="{}"))
    ask(page, "hello")
    expect(page.locator(".notice")).to_contain_text(notice)
    expect(page.locator(".waiting")).to_have_count(0)
    expect(page.locator("textarea")).to_be_enabled()

    page.unroute("**/chat")
    ask(page, "hello again")
    expect(their_bubbles(page)).to_have_count(1)


def test_a_503_shows_the_servers_message_as_text(page):
    """Such as the daily budget's "taking a break until tomorrow"."""
    open_chat(page)
    detail = "Taking a break until tomorrow. <b>Try</b> again then."
    page.route("**/chat", lambda route: route.fulfill(
        status=503, content_type="application/json", body=json.dumps({"detail": detail})))
    ask(page, "hello")
    expect(page.locator(".notice")).to_have_text(detail)
    expect(page.locator(".notice b")).to_have_count(0)


# --- install errors (backlog 78) ------------------------------------------------


def console_errors(page):
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    return errors


def test_unknown_site_key_explains_itself_in_the_console(page):
    errors = console_errors(page)
    page.goto(page.base + "/host?key=pk_wrong")
    page.locator(".launcher").click()
    expect(page.locator(".notice")).to_contain_text("not available")
    expect(page.locator("textarea")).to_be_disabled()
    assert any("[sitechat]" in e and "not recognised" in e for e in errors)


def test_domain_not_allowed_explains_itself_in_the_console(page):
    errors = console_errors(page)
    page.goto(page.base + "/host?key=pk_elsewhere")
    page.locator(".launcher").click()
    expect(page.locator(".notice")).to_contain_text("not available")
    assert any("[sitechat]" in e and "not allowed on localhost" in e for e in errors)


# --- the host page (backlog 56) -------------------------------------------------


def test_host_page_css_does_not_reach_the_widget(page):
    open_chat(page, "/host?hostile=true")
    # The page's own button is red; the widget's are not.
    launcher_bg = page.locator(".launcher").evaluate("e => getComputedStyle(e).backgroundColor")
    assert launcher_bg != "rgb(255, 0, 0)"
    expect(page.locator("textarea")).to_be_visible()
    bubble_colour = page.locator(".turn.them .bubble").first.evaluate(
        "e => getComputedStyle(e).color"
    )
    assert bubble_colour != "rgb(255, 0, 0)"
    ask(page, "hello")
    expect(their_bubbles(page)).to_have_text(["Fine."])


# --- keyboard and accessibility (backlog 57, partly) ----------------------------


def test_keyboard_use(page):
    page.goto(page.base + "/host")
    page.locator(".launcher").focus()
    page.keyboard.press("Enter")
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator("textarea")).to_be_focused()

    page.keyboard.type("hello")
    page.keyboard.press("Shift+Enter")  # a new line, not a send
    expect(page.locator(".turn.you")).to_have_count(0)
    page.keyboard.press("Enter")
    expect(page.locator(".turn.you .bubble")).to_have_text("hello\n")

    page.keyboard.press("Escape")
    expect(page.locator(".panel")).to_be_hidden()
    expect(page.locator(".launcher")).to_be_focused()


def test_screen_reader_labels(page):
    open_chat(page)
    expect(page.locator(".panel")).to_have_attribute("role", "dialog")
    expect(page.locator(".log")).to_have_attribute("aria-live", "polite")
    expect(page.locator("textarea")).to_have_attribute("aria-label", "Your message")
    expect(page.locator(".send")).to_have_attribute("aria-label", "Send")
    expect(page.locator("button[aria-label='Close chat']")).to_be_visible()


# --- Safari engine on a phone (backlog 55) --------------------------------------


def test_iphone_with_webkit(playwright, server):
    browser = launch(playwright, "webkit")
    context = browser.new_context(**playwright.devices["iPhone 13"])
    page = context.new_page()
    page.base = server
    try:
        open_chat(page)
        width = page.viewport_size["width"]
        box = page.locator(".panel").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
        ask(page, "two paragraphs please")
        expect(their_bubbles(page)).to_have_count(2)
        expect(page.locator(".handoff")).to_be_visible()
        # The page itself must not scroll sideways because of the widget.
        assert page.evaluate("document.documentElement.scrollWidth") <= width
    finally:
        context.close()
        browser.close()


# --- the latest message stays in view -------------------------------------------


def fully_visible_in_log(page, locator):
    """Whether the element is entirely inside the chat's visible area."""
    return locator.evaluate("""el => {
        const log = el.closest('.log').getBoundingClientRect();
        const box = el.getBoundingClientRect();
        return box.top >= log.top - 1 && box.bottom <= log.bottom + 1;
    }""")


def test_last_reply_stays_visible_when_the_button_appears(page):
    """The button appears after the first reply and makes the chat area
    shorter; the reply it follows must not end up hidden below the fold."""
    page.set_viewport_size({"width": 400, "height": 640})
    open_chat(page)
    ask(page, "hello " + "and some more words " * 30)  # a long question fills the log
    last = their_bubbles(page).last
    expect(last).to_have_text("Fine.")
    expect(page.locator(".handoff")).to_be_visible()
    page.wait_for_timeout(200)
    assert fully_visible_in_log(page, last)


def test_last_reply_stays_visible_when_the_input_grows(page):
    page.set_viewport_size({"width": 400, "height": 640})
    open_chat(page)
    ask(page, "hello " + "and some more words " * 30)
    last = their_bubbles(page).last
    expect(last).to_have_text("Fine.")
    page.locator("textarea").fill("line\n" * 5)  # the input grows to its maximum
    page.locator("textarea").dispatch_event("input")
    page.wait_for_timeout(200)
    assert fully_visible_in_log(page, last)


def test_a_visitor_reading_back_is_not_pulled_down(page):
    page.set_viewport_size({"width": 400, "height": 640})
    open_chat(page)
    ask(page, "hello " + "and some more words " * 30)
    expect(their_bubbles(page).last).to_have_text("Fine.")
    page.locator(".log").evaluate("log => { log.scrollTop = 0; }")
    page.wait_for_timeout(100)
    page.locator("textarea").fill("line\n" * 5)
    page.locator("textarea").dispatch_event("input")
    page.wait_for_timeout(200)
    assert page.locator(".log").evaluate("log => log.scrollTop") == 0


# --- the launcher teaser --------------------------------------------------------

DEFAULT = "Chat with us"
STEP_MS = 2000  # each question shows for this long (TEASER_STEP_MS)
# The test tenant's short suggested questions (both are under 32 characters).
TEASED = ["What does it cost?", "Who is the founder?"]


def launcher_text(page):
    return page.locator(".launch-text")


def step(page, ms):
    """Let fake time pass, then let the fade (150 ms) finish."""
    page.clock.run_for(ms)
    page.clock.run_for(200)


def test_teaser_shows_questions_then_settles(teaser_page):
    page = teaser_page
    load(page)
    expect(launcher_text(page)).to_have_text(DEFAULT)
    width_before = page.locator(".launcher").evaluate("e => e.offsetWidth")

    step(page, 3000)
    expect(launcher_text(page)).to_have_text(TEASED[0])
    page.wait_for_timeout(450)  # the width transition (real time)
    assert page.locator(".launcher").evaluate("e => e.offsetWidth") > width_before

    page.clock.run_for(STEP_MS // 2)  # halfway: still the same question
    expect(launcher_text(page)).to_have_text(TEASED[0])
    step(page, STEP_MS // 2)
    expect(launcher_text(page)).to_have_text(TEASED[1])
    step(page, STEP_MS)
    expect(launcher_text(page)).to_have_text(DEFAULT)

    # Rests for 30 seconds, then goes round again.
    step(page, 25 * 1000)
    expect(launcher_text(page)).to_have_text(DEFAULT)
    step(page, 5 * 1000)
    expect(launcher_text(page)).to_have_text(TEASED[0])


def test_teaser_holds_while_pointed_at(teaser_page):
    page = teaser_page
    load(page)
    step(page, 3000)
    expect(launcher_text(page)).to_have_text(TEASED[0])

    page.locator(".launcher").hover()
    step(page, 5000)
    expect(launcher_text(page)).to_have_text(TEASED[0])

    page.mouse.move(5, 5)
    step(page, STEP_MS)
    expect(launcher_text(page)).to_have_text(TEASED[1])


def test_clicking_a_teased_question_asks_it_then_the_teaser_resumes(teaser_page):
    page = teaser_page
    load(page)
    step(page, 3000)
    expect(launcher_text(page)).to_have_text(TEASED[0])

    page.locator(".launcher").click()
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".turn.you .bubble")).to_have_text(TEASED[0])
    expect(page.locator(".prompts")).to_have_count(0)

    # Paused while the chat is open.
    step(page, 60_000)
    page.locator("button[aria-label='Close chat']").click()
    expect(launcher_text(page)).to_have_text(DEFAULT)

    # Closed: starts again from the first question, as on page load,
    # though the closed chat left focus on the launcher.
    page.mouse.move(5, 5)
    step(page, 3000)
    expect(launcher_text(page)).to_have_text(TEASED[0])
    step(page, STEP_MS)
    expect(launcher_text(page)).to_have_text(TEASED[1])

    # And on the next visit too: clicking does not turn it off.
    page.reload()
    expect(page.locator(".launcher")).to_have_attribute("aria-label", re.compile("^Chat with"))
    step(page, 3000)
    expect(launcher_text(page)).to_have_text(TEASED[0])


def test_keyboard_focus_holds_the_teaser(teaser_page):
    page = teaser_page
    load(page)
    step(page, 3000)
    expect(launcher_text(page)).to_have_text(TEASED[0])
    page.keyboard.press("Tab")
    expect(page.locator(".launcher")).to_be_focused()
    step(page, 5000)
    expect(launcher_text(page)).to_have_text(TEASED[0])


def test_a_site_can_turn_the_teaser_off(teaser_page, server):
    page = teaser_page
    page.context.add_cookies([{"name": "teaser", "value": "off", "url": server}])
    load(page)
    step(page, 10_000)
    expect(launcher_text(page)).to_have_text(DEFAULT)


def test_clicking_the_default_text_just_opens_the_chat(teaser_page):
    page = teaser_page
    load(page)
    page.locator(".launcher").click()
    expect(page.locator(".panel")).to_be_visible()
    expect(page.locator(".turn.you")).to_have_count(0)


def test_no_teaser_with_reduced_motion(browser, server):
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    page.base = server
    freeze_clock(page)
    load(page)
    step(page, 10_000)
    expect(launcher_text(page)).to_have_text(DEFAULT)
    context.close()


def test_long_questions_are_left_out_and_the_launcher_stays_small(browser, server):
    """Only short questions are teased, and the launcher never grows past
    its cap, even on a narrow phone."""
    context = browser.new_context(viewport={"width": 320, "height": 640})
    page = context.new_page()
    page.base = server
    freeze_clock(page)
    page.route("**/config", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"display_name": "School", "greeting": "Hi", "accent_colour": "#1f6f5c",'
             ' "suggested_questions": ["What exactly is included in the 200-hour fee?",'
             ' "Do you offer a vegan meal plan?"]}',
    ))
    load(page)
    step(page, 3000)
    # The 46-character question is skipped; the 31-character one is shown.
    expect(launcher_text(page)).to_have_text("Do you offer a vegan meal plan?")
    page.wait_for_timeout(450)
    width = page.locator(".launcher").evaluate("e => e.getBoundingClientRect().width")
    assert width <= min(300, 320 - 48) + 1
    assert launcher_text(page).evaluate("e => e.scrollWidth <= e.clientWidth")  # not cut off
    context.close()


def test_a_question_too_wide_for_the_screen_is_skipped_not_cut_off(browser, server):
    """Measured in the page's own font (the widget inherits it), so the
    screen is narrow enough here that the question cannot fit in any."""
    context = browser.new_context(viewport={"width": 250, "height": 640})
    page = context.new_page()
    page.base = server
    freeze_clock(page)
    page.route("**/config", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"display_name": "School", "greeting": "Hi", "accent_colour": "#1f6f5c",'
             ' "suggested_questions": ["What are the dates and fees?", "Any Wi-Fi?"]}',
    ))
    load(page)
    step(page, 3000)
    # 202 px of room: the first question would be cut off, so only the
    # second is teased.
    expect(launcher_text(page)).to_have_text("Any Wi-Fi?")
    step(page, STEP_MS)
    expect(launcher_text(page)).to_have_text(DEFAULT)
    context.close()


# --- contact request confirmation (backlog 82) ----------------------------------


def test_a_contact_request_is_confirmed_with_a_card(page):
    open_chat(page)
    ask(page, "my email is sam@example.com")
    expect(page.locator(".notice")).to_have_text(
        "Your email goes only to Pyramid Yogshala's team, so they can reply to you."
    )
    expect(their_bubbles(page).last).to_have_text("Fine.")
    expect(page.locator(".confirm")).to_have_count(0)

    page.locator(".handoff").click()
    expect(their_bubbles(page).last).to_have_text("Someone from the team will be in touch.")
    card = page.locator(".confirm")
    expect(card).to_have_text(
        "Request received. Pyramid Yogshala will reply to the email address you "
        "gave, usually within 24 hours."
    )
    expect(card).to_have_attribute("role", "status")
    # Under the reply, not before it.
    assert page.locator(".log > *").last.evaluate("e => e.classList.contains('confirm')")

    ask(page, "thanks")
    expect(their_bubbles(page).last).to_have_text("Fine.")
    expect(page.locator(".confirm")).to_have_count(1)  # not repeated


def test_request_before_the_email_is_confirmed_once_it_arrives(page):
    open_chat(page)
    ask(page, "hello")
    page.locator(".handoff").click()
    expect(their_bubbles(page).last).to_have_text("Could you share your email address?")
    expect(page.locator(".confirm")).to_have_count(0)

    ask(page, "sam@example.com")
    expect(page.locator(".confirm")).to_contain_text("Request received.")


def test_a_typed_request_also_retires_the_button(page):
    open_chat(page)
    ask(page, "my email is sam@example.com")
    expect(page.locator(".handoff")).to_be_visible()
    ask(page, "please contact me")
    expect(page.locator(".confirm")).to_contain_text("Request received.")
    expect(page.locator(".handoff")).to_be_hidden()


# --- thumbs up/down (backlog 71) -------------------------------------------------


def feedback_posts(page):
    """Bodies of the widget's POST /feedback requests, as they are sent."""
    posts = []
    page.on("request", lambda r: posts.append(json.loads(r.post_data))
            if r.url.endswith("/feedback") and r.method == "POST" else None)
    return posts


def test_thumbs_come_after_the_whole_reply_and_not_the_greeting(page):
    open_chat(page)
    expect(page.locator(".rate")).to_have_count(0)  # the greeting is not rated
    ask(page, "two paragraphs please")
    expect(their_bubbles(page)).to_have_count(2)
    expect(page.locator(".rate")).to_have_count(1)
    # After the second bubble, not between the two.
    assert page.locator(".log > *").last.evaluate("e => e.className") == "rate"
    expect(page.get_by_role("button", name="Helpful", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Not helpful")).to_be_visible()


def test_a_thumbs_up_is_sent_and_shown(page):
    posts = feedback_posts(page)
    open_chat(page)
    ask(page, "hello")
    up = page.get_by_role("button", name="Helpful", exact=True)
    up.click()
    expect(up).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".rate-thanks")).to_be_visible()
    page.wait_for_timeout(200)
    assert posts and posts[-1]["rating"] == "up" and len(posts[-1]["reply_id"]) == 16


def test_a_thumbs_down_offers_reasons_and_sends_the_one_chosen(page):
    posts = feedback_posts(page)
    open_chat(page)
    ask(page, "hello")
    page.get_by_role("button", name="Not helpful").click()
    reasons = page.locator(".rate-reasons button")
    expect(reasons).to_have_text(["Wrong information", "Didn't answer my question", "Other"])
    reasons.nth(1).click()
    expect(page.locator(".rate-reasons")).to_have_count(0)
    expect(page.locator(".rate-thanks")).to_be_visible()
    page.wait_for_timeout(200)
    assert [p["rating"] for p in posts] == ["down", "down"]
    assert posts[-1]["reason"] == "not_answered"


def test_the_rating_can_be_changed(page):
    posts = feedback_posts(page)
    open_chat(page)
    ask(page, "hello")
    page.get_by_role("button", name="Not helpful").click()
    page.get_by_role("button", name="Helpful", exact=True).click()
    expect(page.get_by_role("button", name="Not helpful")).to_have_attribute("aria-pressed", "false")
    expect(page.locator(".rate-reasons")).to_have_count(0)
    page.wait_for_timeout(200)
    assert posts[-1]["rating"] == "up"


def test_each_reply_gets_its_own_thumbs_with_its_own_id(page):
    posts = feedback_posts(page)
    open_chat(page)
    ask(page, "hello")
    expect(page.locator(".rate")).to_have_count(1)
    ask(page, "hello again")
    expect(page.locator(".rate")).to_have_count(2)
    page.locator(".rate").nth(0).get_by_role("button", name="Helpful", exact=True).click()
    page.locator(".rate").nth(1).get_by_role("button", name="Helpful", exact=True).click()
    page.wait_for_timeout(200)
    assert len({p["reply_id"] for p in posts}) == 2


def test_thumbs_work_from_the_keyboard(page):
    open_chat(page)
    ask(page, "hello")
    up = page.get_by_role("button", name="Helpful", exact=True)
    expect(up).to_be_visible()
    up.focus()
    page.keyboard.press("Enter")
    expect(up).to_have_attribute("aria-pressed", "true")
