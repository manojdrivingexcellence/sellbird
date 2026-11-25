# Daily workflow pilot

This pilot is available from **Discover → Prepare contacts now**. Choose a
source, search scope, spending cap and contact goal (1–10), estimate first,
then start the session. The ordinary background loop must be stopped. The
session prepares drafts in **Outbox** for review even if ordinary sending is
set to Autopilot; it does not send email.

The session card shows search results, already known and excluded companies,
people found, missing or unverified email addresses, verified people and first
drafts awaiting review. Search spend and model usage are shown separately.
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

**Conversations** has separate filters for replies requiring attention,
interested people and declines or opt-outs. **Contacts → Invalid email**
shows unusable addresses, including bounced addresses. A bounce is suppressed
by the existing mail handler; it is not a successful contact.

For customer installations, give each customer their own installation,
database, configuration, mailbox credentials and dashboard token. The local
database deduplicates contacts within that customer's installation. It is not
a cross-customer shared contact registry. Do not copy one customer's database
or mailbox credentials to another installation. A shared managed service
would need authenticated tenant isolation and explicit rules for any
cross-customer suppression before customer data is placed on one server.
