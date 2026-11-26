"""Focused contact preparation never sends and reports its stopping reason."""

from types import SimpleNamespace

import pytest

import sellbird.contact_session as session
import sellbird.dashboard as dash
from sellbird.models.campaign import Campaign, EmailStep
from sellbird.state import StateManager
from tests.test_dashboard_signals import client  # noqa: F401


def test_session_targets_override_only_this_run():
    saved = SimpleNamespace(icp=SimpleNamespace(
        industries=["Clinic"], titles=["Owner"]))
    run = dash._session_target_config(
        {"industries": ["Training institute"], "titles": ["Director"]}, saved)
    assert run.icp.industries == ["Training institute"]
    assert run.icp.titles == ["Director"]
    assert saved.icp.industries == ["Clinic"]
    assert saved.icp.titles == ["Owner"]


def test_dashboard_rejects_invalid_goal_before_starting(client, monkeypatch):
    monkeypatch.setattr(dash, "_check_sellbird_pid", lambda: None)
    response = client.post("/api/contact-session", json={"goal": 0, "provider": "osm"})
    assert response.status_code == 400
    assert "1 to 10" in response.json()["message"]


def test_dashboard_refuses_a_concurrent_background_loop(client, monkeypatch):
    monkeypatch.setattr(dash, "_check_sellbird_pid", lambda: 123)
    response = client.post("/api/contact-session", json={"goal": 5, "provider": "osm"})
    assert response.status_code == 409
    assert "Stop the background loop" in response.json()["message"]


def test_dashboard_starts_one_focused_session_without_starting_autopilot(client, monkeypatch):
    import sellbird.collectors.discover as discover
    import sellbird.config as config_mod

    monkeypatch.setattr(dash, "_check_sellbird_pid", lambda: None)
    monkeypatch.setattr(dash, "_discovery_queries", lambda body, config: [SimpleNamespace(limit=20)])
    monkeypatch.setattr(discover, "estimate_cost", lambda provider, queries: 0.0)
    monkeypatch.setattr(config_mod, "load_config", lambda: SimpleNamespace(
        channels=SimpleNamespace(email=SimpleNamespace(provider="smtp"))))

    async def prepare(state, config, provider, queries, cap, goal, report):
        report.update(phase="done", message="Ready for review", drafts_ready=goal)

    monkeypatch.setattr(session, "prepare_contacts", prepare)
    response = client.post("/api/contact-session", json={"provider": "osm", "goal": 5,
                                                     "limit": 20, "max_spend": 1})
    assert response.status_code == 200 and response.json()["success"]
    # The task runs in TestClient's event loop; it only prepares, never starts
    # the normal background loop or a sender drain.
    import time
    for _ in range(20):
        status = client.get("/api/contact-session").json()
        if not status["running"]:
            break
        time.sleep(0.01)
    assert status["phase"] == "done" and status["drafts_ready"] == 5
    assert dash._check_sellbird_pid() is None


def test_focused_report_survives_dashboard_restart(client, monkeypatch):
    import json
    from sellbird.state import StateManager

    async def save():
        state = StateManager(str(dash.DB_PATH))
        await state.init_db()
        await state.set_setting("focused_session_report", json.dumps({
            "phase": "contacts", "goal": 5, "companies_found": 2,
            "settings": {"provider": "osm", "goal": 5},
        }))

    import asyncio
    asyncio.run(save())
    monkeypatch.setattr(dash, "_contact_session_report", None)
    monkeypatch.setattr(dash, "_contact_session_task", None)
    status = client.get("/api/contact-session").json()
    assert status["phase"] == "interrupted" and not status["running"]
    assert status["companies_found"] == 2


def test_continue_reuses_saved_search_settings(client, monkeypatch):
    import asyncio
    import json
    import sellbird.collectors.discover as discover
    import sellbird.config as config_mod

    monkeypatch.setattr(dash, "_check_sellbird_pid", lambda: None)
    monkeypatch.setattr(dash, "_contact_session_task", None)
    monkeypatch.setattr(dash, "_discovery_queries", lambda body, config: [SimpleNamespace(limit=20)])
    monkeypatch.setattr(discover, "estimate_cost", lambda provider, queries: 0.0)
    monkeypatch.setattr(config_mod, "load_config", lambda: SimpleNamespace(
        channels=SimpleNamespace(email=SimpleNamespace(provider="smtp"))))

    async def save():
        state = StateManager(str(dash.DB_PATH))
        await state.init_db()
        await state.set_setting("focused_session_report", json.dumps({
            "phase": "paused", "goal": 3, "started_at": "2026-11-25T00:00:00+00:00",
            "settings": {"provider": "osm", "goal": 3, "limit": 20, "max_spend": 1},
        }))

    asyncio.run(save())
    seen = {}

    async def prepare(state, config, provider, queries, cap, goal, report, resume=False):
        seen.update(provider=provider, goal=goal, resume=resume)
        report.update(phase="done", message="Continued")

    monkeypatch.setattr(session, "prepare_contacts", prepare)
    response = client.post("/api/contact-session", json={"resume": True})
    assert response.status_code == 200
    import time
    for _ in range(20):
        if seen:
            break
        time.sleep(0.01)
    assert seen == {"provider": "osm", "goal": 3, "resume": True}


def test_continue_reports_quota_block_before_restarting(client, monkeypatch):
    import asyncio
    import json
    import sellbird.config as config_mod
    import sellbird.brain as brain_mod

    monkeypatch.setattr(dash, "_check_sellbird_pid", lambda: None)
    monkeypatch.setattr(dash, "_contact_session_task", None)
    monkeypatch.setattr(config_mod, "load_config", lambda: SimpleNamespace(
        channels=SimpleNamespace(email=SimpleNamespace(provider="smtp")),
        brain=SimpleNamespace(provider="codex"),
        usage=SimpleNamespace(max_daily_claude_percent=80)))

    async def save():
        state = StateManager(str(dash.DB_PATH))
        await state.init_db()
        await state.set_setting("focused_session_report", json.dumps({
            "phase": "paused", "message": "Codex usage is 81%, at or above the configured 80% ceiling.",
            "settings": {"provider": "osm", "goal": 5}}))
    asyncio.run(save())

    class OverBudget:
        def __init__(self, state, config):
            self.budget_reason = "Codex usage is 81%, at or above the configured 80% ceiling."

        async def preflight(self):
            return ""

        async def is_within_budget(self, calls, percent):
            return False

    monkeypatch.setattr(brain_mod, "Brain", OverBudget)
    response = client.post("/api/contact-session", json={"resume": True})
    assert response.status_code == 409
    assert "81%" in response.json()["message"]
    assert client.get("/api/contact-session").json()["phase"] == "paused"
    check = client.get("/api/contact-session/resume-check")
    assert check.status_code == 200
    assert check.json()["can_resume"] is False


@pytest.mark.asyncio
async def test_session_reports_model_pause_and_does_not_write(monkeypatch):
    class FakeState:
        async def get_prospects_by_status(self, status):
            return []

    class FakeBrain:
        def __init__(self, *args):
            pass

        async def preflight(self):
            return "Codex login needs renewal"

    class FakeScout:
        def __init__(self, *args):
            pass

        async def run(self):
            raise AssertionError("Scout must not run with an unavailable model")

    async def ids(state, table):
        return set()

    async def discovery(*args, **kwargs):
        return SimpleNamespace(discover={"new_companies": 2}, errors=[], as_dict=lambda: {})

    monkeypatch.setattr(session, "_ids", ids)
    monkeypatch.setattr(session, "Brain", FakeBrain)
    monkeypatch.setattr(session, "Scout", FakeScout)
    monkeypatch.setattr(session, "run_prospecting", discovery)
    monkeypatch.setattr(session, "load_env", lambda: None)
    config = SimpleNamespace(brain=None, usage=SimpleNamespace(max_daily_claude_percent=30))
    progress = {}
    await session.prepare_contacts(FakeState(), config, "osm", [], 1.0, 5, progress)
    assert progress["phase"] == "paused"
    assert progress["companies_found"] == 2
    assert "login" in progress["message"]


@pytest.mark.asyncio
async def test_session_stages_only_its_new_verified_contact_for_review(monkeypatch, tmp_path):
    from tests.test_outbox_native import seed_prospect, _now_iso
    state = StateManager(str(tmp_path / "session.db"))
    await state.init_db()
    seen = {"scout": 0, "written_ids": None, "sent": False}

    class FakeBrain:
        paused_reason = ""

        def __init__(self, *args):
            pass

        async def preflight(self):
            return ""

        async def is_within_budget(self, *args):
            return True

    class FakeScout:
        def __init__(self, *args):
            pass

        async def run(self):
            seen["scout"] += 1
            if seen["scout"] == 1:
                await seed_prospect(state, email="fresh@example.com", status="new")

    class FakeWriter:
        def __init__(self, *args):
            pass

        async def run(self, only_ids=None):
            seen["written_ids"] = only_ids
            await state.add_campaign(Campaign(id="", name="Focused", channel="email",
                sequence=[EmailStep(step=1, subject="Hello", body="Hello there.", delay_days=0)],
                prospect_ids=list(only_ids), status="draft"))

    class FakeSender:
        is_native = True

        def __init__(self, *args):
            self.inboxes = None

        async def _load_inboxes(self):
            return object()

        async def _stage_campaign_native(self, campaign, approval_override=False):
            assert approval_override is True
            seen["sent"] = False  # this path stages; it never calls a mail provider
            await state.add_outbox_item(prospect_id=campaign.prospect_ids[0],
                campaign_id=campaign.id, step=1, to_email="fresh@example.com",
                subject="Hello", body="Hello there.", send_at=_now_iso(), status="pending_review")

    async def discovery(*args, **kwargs):
        return SimpleNamespace(discover={"new_companies": 1}, errors=[], as_dict=lambda: {})

    monkeypatch.setattr(session, "Brain", FakeBrain)
    monkeypatch.setattr(session, "Scout", FakeScout)
    monkeypatch.setattr(session, "Writer", FakeWriter)
    monkeypatch.setattr(session, "Sender", FakeSender)
    monkeypatch.setattr(session, "run_prospecting", discovery)
    monkeypatch.setattr(session, "load_env", lambda: None)
    config = SimpleNamespace(brain=None, usage=SimpleNamespace(max_daily_claude_percent=30))
    progress = {}
    await session.prepare_contacts(state, config, "osm", [], 1.0, 1, progress)
    assert progress["phase"] == "done" and progress["drafts_ready"] == 1
    assert len(seen["written_ids"]) == 1 and seen["sent"] is False


@pytest.mark.asyncio
async def test_continued_session_stages_existing_draft_without_repeating_discovery(monkeypatch, tmp_path):
    from tests.test_outbox_native import seed_prospect, _now_iso
    state = StateManager(str(tmp_path / "continued.db"))
    await state.init_db()
    prospect_id = await seed_prospect(state, email="saved@example.com", status="queued")
    campaign = Campaign(id="", name="Saved draft", channel="email",
        sequence=[EmailStep(step=1, subject="Hello", body="Hello there.", delay_days=0)],
        prospect_ids=[prospect_id], status="draft")
    await state.add_campaign(campaign)

    class FakeBrain:
        paused_reason = ""
        def __init__(self, *args): pass
        async def preflight(self): return ""
        async def is_within_budget(self, *args): return True

    class FakeWriter:
        def __init__(self, *args): pass
        async def run(self, only_ids=None):
            assert only_ids == {prospect_id}

    class FakeSender:
        is_native = True
        def __init__(self, *args): self.inboxes = None
        async def _load_inboxes(self): return object()
        async def _stage_campaign_native(self, campaign, approval_override=False):
            assert approval_override
            await state.add_outbox_item(prospect_id=prospect_id,
                campaign_id=campaign.id, step=1, to_email="saved@example.com",
                subject="Hello", body="Hello there.", send_at=_now_iso(), status="pending_review")

    async def forbidden(*args, **kwargs):
        raise AssertionError("A continued session must not repeat completed discovery or Scout")

    monkeypatch.setattr(session, "Brain", FakeBrain)
    monkeypatch.setattr(session, "Writer", FakeWriter)
    monkeypatch.setattr(session, "Sender", FakeSender)
    monkeypatch.setattr(session, "run_prospecting", forbidden)
    monkeypatch.setattr(session, "load_env", lambda: None)
    monkeypatch.setattr(session.Scout, "run", forbidden)
    progress = {"discovery_complete": True, "baseline_people": [],
                "baseline_campaigns": [], "selected_ids": [prospect_id]}
    config = SimpleNamespace(brain=None, usage=SimpleNamespace(max_daily_claude_percent=30))
    await session.prepare_contacts(state, config, "osm", [], 1, 1, progress, resume=True)
    assert progress["phase"] == "done" and progress["drafts_ready"] == 1
