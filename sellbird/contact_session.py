"""One operator-started prospecting pass. It never sends email."""

from sellbird.agents.scout import Scout
from sellbird.agents.sender import Sender
from sellbird.agents.writer import Writer
from sellbird.brain import Brain
from sellbird.config import load_env
from sellbird.pipeline import run_prospecting


async def _ids(state, table: str) -> set[str]:
    # Both table names are fixed below, never taken from a request.
    assert table in ("prospects", "campaigns")
    async with state._connect() as db:
        async with db.execute(f"SELECT id FROM {table}") as cursor:
            return {row[0] for row in await cursor.fetchall()}


async def prepare_contacts(state, config, provider, queries, max_spend, goal, progress):
    """Find contacts and stage up to `goal` first emails for human review.

    Discovery may find more businesses or people than the goal; only up to
    `goal` newly verified people are passed to Writer. A failed verification,
    model pause or lack of candidates ends with an explicit reason.
    """
    env = load_env()
    brain = Brain(state, config.brain)
    before_people = await _ids(state, "prospects")
    before_campaigns = await _ids(state, "campaigns")
    progress.update(phase="discover", message="Finding businesses and reading their websites.")
    report = await run_prospecting(state, config, provider, queries, max_spend=max_spend)
    progress["companies_found"] = (report.discover or {}).get("new_companies", 0)
    progress["discovery"] = report.as_dict()
    if report.errors:
        progress["message"] = "; ".join(report.errors[:2])[:400]

    scout = Scout(brain, state, config, env)
    verified = []
    no_gain = 0
    # A single Scout pass can find two or three people. Continue in this
    # operator-started session, without the normal heartbeat's long sleep.
    for attempt in range(5):
        problem = await brain.preflight()
        if problem:
            progress.update(phase="paused", message=str(problem))
            return
        max_calls = max(int(200 * config.usage.max_daily_claude_percent / 100), 1)
        if not await brain.is_within_budget(max_calls, config.usage.max_daily_claude_percent):
            progress.update(phase="paused", message=getattr(brain, "budget_reason", "") or "Model budget reached.")
            return
        progress.update(phase="contacts", message=f"Checking people and email addresses (pass {attempt + 1} of 5).")
        await scout.run()
        candidates = await state.get_prospects_by_status("new")
        verified = [p for p in candidates if p.id not in before_people and p.email
                    and p.email_status == "verified" and not await state.is_suppressed(p.email)]
        progress["verified_contacts"] = len(verified)
        if len(verified) >= goal:
            break
        no_gain = no_gain + 1 if len(verified) == progress.get("previous_verified", 0) else 0
        progress["previous_verified"] = len(verified)
        if no_gain >= 2:
            break

    selected = {p.id for p in verified[:goal]}
    if not selected:
        progress.update(phase="done", message=progress.get("message") or
                        "No new verified email addresses found. Review Contacts and your verifier settings.")
        return
    problem = await brain.preflight()
    if problem or not await brain.is_within_budget(
            max(int(200 * config.usage.max_daily_claude_percent / 100), 1),
            config.usage.max_daily_claude_percent):
        progress.update(phase="paused", message=str(problem or getattr(brain, "budget_reason", "Model budget reached.")))
        return
    progress.update(phase="writing", message=f"Writing drafts for up to {len(selected)} verified contacts.")
    await Writer(brain, state, config, env).run(only_ids=selected)
    if getattr(brain, "paused_reason", ""):
        progress.update(phase="paused", message=brain.paused_reason)
        return

    sender = Sender(brain, state, config, env)
    if not sender.is_native:
        progress.update(phase="done", message="Drafts are in Campaigns; this mailbox cannot stage individual Outbox approvals.")
        return
    progress.update(phase="staging", message="Putting drafts in the Outbox for your approval.")
    sender.inboxes = await sender._load_inboxes()
    try:
        for campaign in await state.get_campaigns_by_status("draft"):
            if campaign.id not in before_campaigns:
                await sender._stage_campaign_native(campaign, approval_override=True)
    finally:
        sender.inboxes = None
    async with state._connect() as db:
        marks = ",".join("?" for _ in selected)
        async with db.execute(
            f"SELECT COUNT(*) FROM outbox WHERE prospect_id IN ({marks}) "
            "AND step = 1 AND status = 'pending_review'", tuple(selected),
        ) as cursor:
            progress["drafts_ready"] = (await cursor.fetchone())[0]
    progress.update(phase="done", message=(
        f"{progress['drafts_ready']} first emails are waiting in Outbox for your approval."
        if progress["drafts_ready"] else "Verified contacts were found, but no drafts could be staged. Check Activity."))
