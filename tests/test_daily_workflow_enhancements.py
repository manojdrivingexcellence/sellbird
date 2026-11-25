"""Decision evidence, reply review and opted-in agency deduplication."""

import asyncio
import sqlite3

import pytest

from sellbird.agency_registry import AgencyRegistry, configured_registry
from sellbird.models.conversation import Conversation
from sellbird.state import StateManager
from tests.test_dashboard_signals import client  # noqa: F401
from tests.test_outbox_native import seed_prospect


@pytest.mark.asyncio
async def test_lead_decisions_are_scoped_to_run(tmp_path):
    state = StateManager(str(tmp_path / "local.db"))
    await state.init_db()
    await state.record_lead_decisions("run-one", [{"name": "Example", "domain": "example.com",
        "source_url": "https://example.com", "query": "clinics Bengaluru", "reason": "already_known"}])
    assert len(await state.lead_decisions("run-one")) == 1
    assert await state.lead_decisions("run-two") == []


def test_dashboard_exposes_individual_decisions_and_contact_evidence(client, monkeypatch):
    import json
    import sellbird.dashboard as dash
    from sellbird.models.prospect import Prospect

    async def seed():
        state = StateManager(str(client.db_path))
        await state.init_db()
        await state.record_lead_decisions("run-test", [{"name": "Excluded Shop",
            "domain": "excluded.example", "reason": "do_not_contact",
            "source_url": "https://example.org/shop", "query": "shops"}])
        pid = await state.add_prospect(Prospect(first_name="A", last_name="B", title="Owner",
            company="Shop", email="", source_url="https://example.org/person"))
        await state.set_setting("focused_session_report", json.dumps({
            "phase": "done", "discovery_complete": True, "baseline_people": [],
            "discovery": {"discover": {"run_id": "run-test"}}}))
        return pid

    pid = asyncio.run(seed())
    monkeypatch.setattr(dash, "_contact_session_report", None)
    decisions = client.get("/api/contact-session/decisions").json()
    assert decisions["companies"][0]["reason"] == "do_not_contact"
    assert decisions["contacts"][0]["reason"] == "missing_email"
    evidence = client.get(f"/api/prospects/{pid}/evidence").json()
    assert evidence["contact"]["source_url"] == "https://example.org/person"


def test_reply_queue_can_be_reviewed_without_closing_conversation(client):
    async def seed():
        state = StateManager(str(client.db_path))
        await state.init_db()
        pid = await seed_prospect(state, email="interested@example.com")
        await state.add_conversation(Conversation(id="reply-1", prospect_id=pid,
            intent="interested", status="open"))
        await state.add_conversation(Conversation(id="reply-2", prospect_id=pid,
            intent="escalate", status="needs_human"))

    asyncio.run(seed())
    queue = client.get("/api/reply-queue").json()
    assert {q["id"] for q in queue} == {"reply-1", "reply-2"}
    assert client.post("/api/reply-queue/reply-2/reviewed").status_code == 409
    assert client.post("/api/reply-queue/reply-1/reviewed").json()["success"]
    assert {q["id"] for q in client.get("/api/reply-queue").json()} == {"reply-2"}
    assert next(c for c in client.get("/api/conversations").json() if c["id"] == "reply-1")["status"] == "open"
    async def new_reply():
        state = StateManager(str(client.db_path))
        async with state._connect() as db:
            await db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                             ("2030-01-01T00:00:00.123456", "reply-1"))
            await db.commit()
    asyncio.run(new_reply())
    assert {q["id"] for q in client.get("/api/reply-queue").json()} == {"reply-1", "reply-2"}


@pytest.mark.asyncio
async def test_agency_registry_holds_other_customer_without_storing_email(tmp_path, monkeypatch):
    path = tmp_path / "shared" / "registry.db"
    key = "a-shared-secret-that-has-more-than-32-characters"
    first = AgencyRegistry(str(path), key, "customer-a")
    second = AgencyRegistry(str(path), key, "customer-b")
    assert await first.reserve("NAME@Example.com")
    assert await first.reserve("name@example.com")  # same customer follow-up
    assert not await second.available("name@example.com")
    assert not await second.reserve("NAME@example.com")
    with sqlite3.connect(path) as db:
        assert "name@example.com" not in str(db.execute("SELECT * FROM recipients").fetchall())
    monkeypatch.setenv("SELLBIRD_AGENCY_REGISTRY_PATH", str(path))
    monkeypatch.setenv("SELLBIRD_AGENCY_REGISTRY_KEY", key)
    monkeypatch.setenv("SELLBIRD_AGENCY_WORKSPACE", "customer-b")
    assert configured_registry() is not None
    state = StateManager(str(tmp_path / "customer-b.db"))
    await state.init_db()
    assert await state.prospect_exists(email="name@example.com")


@pytest.mark.asyncio
async def test_agency_registry_atomic_claim(tmp_path):
    path = str(tmp_path / "shared.db")
    key = "another-shared-secret-with-over-32-characters"
    a = AgencyRegistry(path, key, "a")
    b = AgencyRegistry(path, key, "b")
    results = await asyncio.gather(a.reserve("same@example.com"), b.reserve("same@example.com"))
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_native_sender_holds_second_customers_email(tmp_path, monkeypatch):
    from tests.test_outbox_native import FakeProvider, make_sender, seed_campaign
    path = str(tmp_path / "registry.db")
    monkeypatch.setenv("SELLBIRD_AGENCY_REGISTRY_PATH", path)
    monkeypatch.setenv("SELLBIRD_AGENCY_REGISTRY_KEY", "a-secret-shared-by-both-customers-with-32-chars")
    providers = []
    for customer in ("one", "two"):
        monkeypatch.setenv("SELLBIRD_AGENCY_WORKSPACE", customer)
        state = StateManager(str(tmp_path / f"{customer}.db"))
        await state.init_db()
        pid = await seed_prospect(state, email="same@example.com")
        await seed_campaign(state, [pid])
        provider = FakeProvider()
        providers.append(provider)
        await make_sender(state, provider, require_approval=False)._run_native()
    assert len(providers[0].sent) == 1
    assert providers[1].sent == []


def test_daily_summary_keeps_sessions_after_new_run(client):
    import sellbird.dashboard as dash
    from datetime import datetime, timezone

    async def seed():
        state = StateManager(str(client.db_path))
        await state.init_db()
        for n in range(2):
            await state.save_focused_session({"session_id": f"run-{n}",
                "started_at": datetime.now(timezone.utc).isoformat(), "phase": "done",
                "companies_found": 2, "verified_contacts": 1, "drafts_ready": 1})
    asyncio.run(seed())
    result = client.get("/api/daily-summary?tz=330").json()
    assert result["sessions"] == 2
    assert result["totals"]["companies_found"] == 4
    assert result["totals"]["drafts_ready"] == 2


@pytest.mark.asyncio
async def test_dashboard_reads_current_customer_home(tmp_path):
    import sellbird.dashboard as dash
    from sellbird.workspace_home import Home, use_home
    first = Home.for_root(tmp_path / "first")
    second = Home.for_root(tmp_path / "second")
    with use_home(first):
        state = dash._state()
        await state.init_db()
        await state.set_setting("example", "first only")
        assert await dash.query_db("SELECT value FROM settings WHERE key = ?", ("example",)) == [
            {"value": "first only"}]
    with use_home(second):
        state = dash._state()
        await state.init_db()
        assert await dash.query_db("SELECT value FROM settings WHERE key = ?", ("example",)) == []
