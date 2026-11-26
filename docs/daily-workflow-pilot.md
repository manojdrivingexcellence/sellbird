# Daily workflow pilot

This pilot is available from **Discover → Prepare contacts now**. Choose a
source, search scope, spending cap and contact goal (1–10), estimate first,
then start the session. The ordinary background loop must be stopped. The
session prepares drafts in **Outbox** for review even if ordinary sending is
set to Autopilot; it does not send email.

The session card shows search results, already known and excluded companies,
people found, missing or unverified email addresses, verified people and first
drafts awaiting review. Search spend and model usage are shown separately.
**Why were leads skipped?** opens the individual search-result decision log
(do-not-contact, junk, known, duplicate or missing identity) and unusable
new contacts with their source links. **Today** totals all focused sessions
started on your local day, including their outcome and cost. The dashboard
keeps this history in the installation's database.
It can finish with fewer drafts than the goal. Discovery can find more
companies than the contact goal, and the five Scout passes can stop when no
new verified addresses are found or the model budget is exhausted.

If you stop it, the model pauses, or the dashboard restarts, open Discover
and use **Continue session**. The saved baseline, completed discovery and
selected people are reused when available. A restart during an in-progress
search can repeat that search; the existing company and contact deduplication
prevents duplicate records. The session report is saved in the installation's
database. Already staged drafts remain in the Outbox. A new session starts
a fresh search and a fresh contact goal.

On **Contacts**, review email status, role, source link and notes. In **Outbox**,
each draft shows company and contact context, source links, signals and the
verification status. An address marked verified is still worth checking
against the linked evidence before approval. Select individual drafts or
use the bulk approval control after reviewing them.
Open a contact's **Evidence** panel for source pages, email domain check
details, company website and the source behind its buying signals. A source
link is evidence to inspect, not a guarantee the address reaches that person.

**Conversations** has separate filters for replies requiring attention,
interested people and declines or opt-outs. **Contacts → Invalid email**
shows unusable addresses, including bounced addresses. A bounce is suppressed
by the existing mail handler; it is not a successful contact.
The **Reply action queue** lists new interested replies and declines for
review. Mark these reviewed after acting on them. A handoff marked **Needs
you** remains in the queue until you reply from your mailbox and close or
hand back the conversation. A later reply makes a reviewed thread appear
again.

For customer installations, give each customer their own installation,
database, configuration, mailbox credentials and dashboard token. The local
database deduplicates contacts within that customer's installation. Do not
copy one customer's database or mailbox credentials to another installation.

If you operate multiple customer installations on the **same machine or a
shared filesystem**, you can opt in to an agency-wide recipient registry in
each installation's `.env`:

```text
SELLBIRD_AGENCY_REGISTRY_PATH=/absolute/private/path/agency-recipients.db
SELLBIRD_AGENCY_REGISTRY_KEY=<same random secret of at least 32 characters>
SELLBIRD_AGENCY_WORKSPACE=<unique name for this customer>
```

Use the same registry path and secret in every participating installation,
and a different workspace name for each. Before adding leads to Instantly or
sending through a native mailbox, Sellbird reserves a keyed fingerprint of
the recipient. Another participating customer installation skips or holds
that address. Follow-ups within the original customer are allowed. This is
conservative: an uncertain send or failed API call can keep an address
reserved. The registry does not store names or raw email addresses, and it
does not work between separate machines without a shared file. Installations
that do not opt in are not protected by it. This is not a hosted multi-tenant
service; customer logins and shared server isolation need separate work.
