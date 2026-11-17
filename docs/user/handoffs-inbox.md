# Handoffs, inbox and notifications

A **handoff** passes your current work to an agent (or a colleague) so it can continue where you stopped. Your **inbox** is where agents' suggestions wait for your decision, and **notifications** tell you when something needs you. Terms are in the [glossary](../glossary.md#collaboration).

## Handoffs

A handoff is a short code (such as `K7MX-4PQ2`) with a link (`/h/K7MX-4PQ2`). It bundles a goal, a brief you write, the papers involved and, for a chat, the conversation so far. An agent loads it with its access token ([agents and tokens](agents-and-tokens.md)); a person opens the link.

Three things to know first:

- **A handoff is a snapshot.** There is no live mode. The papers, passages and chat transcript are frozen when you create it. Later changes to a chat or a collection do not appear in it.
- **Access is checked every time it is opened.** Who may load it, which papers they may see and what text is withheld are worked out again for each person on each load. This applies to the list of papers, the passages and the citations. The chat transcript text, the brief, the goal, the title and any notes are shown as written, and notes are not checked against labels. Share a handoff that contains a chat only with people who may see everything the chat discusses.
- **It expires.** By default after 7 days. You can revoke it earlier.

### Creating a handoff

Choose **Continue in agent** in one of these places:

| Where | What is handed over |
|---|---|
| Under a finished answer in [chat](chat.md#after-an-answer) (owners only) | The whole chat transcript and the passages it cited that you can see |
| The reader toolbar | That paper |
| Library, filtered to exactly one collection | The collection's papers as they are now |
| Library, otherwise | The query, the filters and the results on the current page |

An agent can look up notes and references itself, using its own access.

The dialog is **Continue in an agent**:

| Field | Meaning |
|---|---|
| **Title** | Up to 200 characters. Empty means the chat or collection name |
| **Goal** | Up to 2000 characters: what the agent should do next |
| **Brief the agent starts from** | Up to 20000 characters of context. It is prefilled from the chat or search; edit it freely. Everything here is shown to the agent and to recipients as plain text, so keep secrets out |
| **Who can load it** | **Only me**, **Named people or groups** or **Everyone signed in** |
| **Expires** | In 1, 7, 30 or 90 days. Seven is the default |

For **Named people or groups**, add groups (org-unit paths starting with `/`) and people (by user ID). You need at least one, and at most 100 in all. You can always load your own handoff. Nothing in the audience setting widens access: an agent still signs in as a user and sees only what that user can see.

After **Create handoff link** you get the code and two buttons: **Copy link** and **Copy agent prompt** ("Using the rOracle MCP server, load handoff K7MX-4PQ2 and continue: your goal"). Paste the prompt into your agent. The link is relative to your rOracle address unless your site has a public address configured.

<!-- screenshot: handoff-dialog: the Continue in an agent dialog with audience and expiry fields -->

### What gets redacted when someone loads it

Whoever opens it, each person (or agent) gets a version cut down to what they may see:

- **Papers you cannot see** are dropped. The recipient is told only a count: "N sources withheld: you don't have access". The paper list never shows their titles or ids, but a chat transcript, the brief or a note may still mention them in its text.
- **Papers labelled Block agents** are dropped for everyone, including admins and the person who made the handoff ([labels](labels.md#what-each-gate-means-to-you)).
- **Text of papers labelled Exclude from AI** is withheld when an agent loads the handoff with an access token. The paper is still listed, flagged as text withheld.
- **Passages and chat citations** are re-checked per loader. A passage from an older version of a paper the loader can see still shows. Citations to community notes are never carried in a handoff.
- **Chat and collection source handles** (the ids of your chat or collection) are shown only to you, not to recipients.

Each time a handoff is loaded the load is recorded in the audit log.

### Opening a handoff

A recipient opens the link. The page shows the title, who made it, who can load it and when it expires, then the goal, the brief, the papers (10 at a time, each with its sensitivity label), how many cited passages are included for the agent, how many sources were withheld, and any notes agents added (the latest 20). **Copy link** and **Copy agent prompt** work here too.

If it cannot be opened you see one of:

- "This handoff was not found or not shared with you. Check the code, or ask whoever shared it."
- "This handoff has expired. Ask whoever shared it for a new one."
- "This handoff was revoked by the person who made it."

Agents can add short notes to a handoff they can load (up to 200 per handoff) if their token has write access. People cannot add notes in the app.

<!-- screenshot: handoff-landing: the handoff page showing goal, brief, documents with labels and a withheld count -->

### Managing your handoffs

Open **Settings** from the avatar menu and choose the **Handoffs** tab. It lists every handoff you made, including ones made by agents for you ("Made by an agent"), with its kind (Chat, Collection, Search, Document or Agent), audience, and **Expires** date or **Expired** or **Revoked**. For an active handoff the row has:

- **Copy code** and **Copy link**.
- **Edit**: change the title, goal, brief, audience, the people and groups, or the expiry. For a handoff already shared with named people, leave both fields empty to keep the list; adding anyone replaces it. **Keep current** leaves the expiry; otherwise it counts days from now. Edits apply the next time someone loads it. The papers, passages and transcript do not change.
- **Revoke**: the link stops working for everyone, agents included. It cannot be undone.

<!-- screenshot: settings-handoffs: the Handoffs tab in Settings with active, expired and revoked rows -->

## Agent proposals

Agents with a write token cannot change your library directly. They can only **propose** notes, replies to notes and sensitivity-label changes, and nothing is published until you decide. Proposals are in **Agent proposals** in the sidebar, where a number badge counts those waiting. The pending list refreshes every minute and when you return to the tab.

- Tabs: **Pending**, **Approved** and **Rejected**. Proposals are grouped by agent: the heading shows the name of the access token the agent used (with a `pat:` prefix). An agent can have at most 50 proposals waiting at once.
- Each card shows the kind (comment, finding, correction, question, relation, **Reply**, **Label change** or **Note label change**), the paper and page it is about, the quote it is anchored to, the proposed visibility and the text. The text is the agent's, shown as plain text and not to be trusted.
- **Approve** publishes it as it is. A note becomes yours, marked **Agent-assisted**, with the visibility the agent proposed (private if it proposed none). **Open note** then jumps to it.
- **Edit** lets you change the text, and for a new note its visibility (and, for **Named people or groups**, the people and groups), before **Approve with edits**. A reply follows the visibility of the note it answers.
- **Reject** discards it. It moves to the Rejected tab.
- **Approve selected** and **Approve all** (in the page header) approve several at once without edits, in batches of 100. A message says how many were approved and why any failed; the failed ones stay selected so you can retry.

Label proposals show the current and proposed labels. Raising a label is allowed for the paper's owner or an admin; lowering is marked "Lowers the label: admins only", needs an admin, and an admin gives a reason when the label requires one ([labels](labels.md#labels-proposed-by-agents)). A proposal about a paper that is gone shows "Document no longer available". Agents never choose a label for a new note; it inherits the paper's (they can only propose a **Note label change** for an existing one).

For what happens to approved notes, see [notes and annotations](notes-annotations.md).

<!-- screenshot: agent-proposals: the Agent proposals page with a pending note and a label change -->

## Notifications

The bell in the top bar shows how many notifications are unread (99+ when more). It shows the latest 10 with **Mark all read** and **See all**, and refreshes every minute and when you return to the tab. **See all** opens the **Notifications** page with **Unread only**, **Mark all read** and **Load more** (30 at a time).

Choosing a notification marks it read and takes you to what it is about. A note reply or a new note on your paper opens the Notes tab at that note. The row menu has **Open** and **Mark as read**.

You may see:

| Notification | Where it leads |
|---|---|
| "*Title* is ready to search." | The paper |
| "*file* looks like *title*. Choose whether it's a new version." | My uploads ([near-duplicates](uploads.md#near-duplicates)) |
| "*file* couldn't be processed" | My uploads |
| "*agent* proposed a note." (any proposal) | Agent proposals |
| "Someone replied to your note." / "Someone added a note to your document." | The note |
| "*Draft* was approved and published." / "was sent back." (with the reviewer's note) | The draft ([writing](writing-drafts.md#submitting)) |
| "*Draft* is waiting for your review." (admins) | The draft |
| Admin-only: failed jobs, connector runs | The admin pages |

Some events, such as a label change on your paper, a deleted paper or a transferred paper, show the generic text "Something changed."

<!-- screenshot: notifications-bell: the bell popover with unread items and the Notifications page -->

## Related

- [Agents and tokens](agents-and-tokens.md)
- [Chat](chat.md)
- [Collections and sharing](collections-sharing.md)
- [Sensitivity labels](labels.md)
