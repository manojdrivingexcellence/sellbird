"""One operator-started prospecting pass. It never sends email."""

import json

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


async def _checkpoint(state, progress):
    # A dashboard state has persistent settings. Lightweight test doubles may
    # omit them; the dashboard also saves the final report in all cases.
    if hasattr(state, "set_setting"):
        await state.set_setting("focused_session_report", json.dumps(progress))


async def prepare_contacts(state, config, provider, queries, max_spend, goal, progress, resume=False):
    """Find contacts and stage up to `goal` first emails for human review.

    Discovery may find more businesses or people than the goal; only up to
    `goal` newly verified people are passed to Writer. A failed verification,
    model pause or lack of candidates ends with an explicit reason.
    """
    env = load_env()
    brain = Brain(state, config.brain)
    before_people = (set(progress["baseline_people"]) if resume and "baseline_people" in progress
                     else await _ids(state, "prospects"))
    before_campaigns = (set(progress["baseline_campaigns"]) if resume and "baseline_campaigns" in progress
                        else await _ids(state, "campaigns"))
    progress["baseline_people"] = sorted(before_people)
    progress["baseline_campaigns"] = sorted(before_campaigns)
    # Save the baseline before any discovery call. If the process dies during
    # that call, a continued session still recognizes its newly saved people.
    await _checkpoint(state, progress)
    if not (resume and progress.get("discovery_complete")):
        progress.update(phase="discover", message="Finding businesses and reading their websites.")
        report = await run_prospecting(state, config, provider, queries, max_spend=max_spend)
        progress["companies_found"] = (report.discover or {}).get("new_companies", 0)
        progress["discovery"] = report.as_dict()
        progress["search_cost_usd"] = round(float((report.discover or {}).get("actual_cost") or 0), 4)
        progress["discovery_complete"] = True
        await _checkpoint(state, progress)
        if report.errors:
            progress["message"] = "; ".join(report.errors[:2])[:400]

    scout = Scout(brain, state, config, env)
    verified = []
    no_gain = 0
    # A single Scout pass can find two or three people. Continue in this
    # operator-started session, without the normal heartbeat's long sleep.
    for attempt in range(0 if resume and progress.get("selected_ids") else 5):
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
        new_people = [p for p in candidates if p.id not in before_people]
        progress["contacts_found"] = len(new_people)
        progress["missing_email"] = sum(not p.email for p in new_people)
        progress["unverified_email"] = sum(bool(p.email) and p.email_status != "verified" for p in new_people)
        verified = [p for p in candidates if p.id not in before_people and p.email
                    and p.email_status == "verified" and not await state.is_suppressed(p.email)]
        progress["verified_contacts"] = len(verified)
        progress["suppressed_email"] = max(0, len(new_people) - progress["missing_email"]
                                            - progress["unverified_email"] - len(verified))
        if len(verified) >= goal:
            break
        no_gain = no_gain + 1 if len(verified) == progress.get("previous_verified", 0) else 0
        progress["previous_verified"] = len(verified)
        if no_gain >= 2:
            break

    selected = (set(progress["selected_ids"]) if resume and progress.get("selected_ids")
                else {p.id for p in verified[:goal]})
    progress["selected_ids"] = sorted(selected)
    await _checkpoint(state, progress)
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
    # Writer excludes already queued contacts; the original baseline is kept
    # so a continued run can stage drafts created before the interruption.
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
