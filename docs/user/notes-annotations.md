# Notes and annotations

Notes let you write on a paper for yourself, for named people or groups, or for everyone who can see the paper. They live on the reader's **Notes** tab and, when anchored, also appear as highlights on the page.

<!-- screenshot: reader-notes: the Notes tab showing a finding with evidence, a question and replies -->

## Note kinds

Choose the kind when you create a note. You can filter the tab by kind with **Filter by kind**.

| Kind | Use it for |
|---|---|
| Comment | General remarks |
| Finding | A result you obtained or confirmed. Findings can carry evidence (below) |
| Correction | An error in the paper |
| Question | Something for other readers to answer |
| Relation | A link to another paper: pick a relation (extends, contradicts, replicates, supersedes) and the target paper |

A relation needs the target paper; you cannot post one without it. Replies are always plain comments and follow the visibility of the note they reply to.

## Writing a note

1. Open the paper. Choose **Add note**, then click the passage you want to note. Esc cancels, and **Write without picking** skips the click. Alternatively, open the Notes tab and choose **New note**.
2. Pick the kind, optionally paste a quote and a page number, write the note (up to 10,000 characters; Markdown links and lists work) and choose **Post note**.
3. Choose who sees it with **Visible to**: **Everyone** (the default), **Named people or groups**, or **Only me**. For named people or groups, add user IDs or group paths (a group path starts with `/`, for example `/Mathematics/Spectral Theory`); at most 50 in total, and at least one is required.
4. Optionally raise its sensitivity label. A note can be more sensitive than its paper, never less ([labels](labels.md)).

When you post a note visible to everyone on a paper someone else owns, the owner is notified. Private and shared notes never notify the owner.

**Add note** only works on the latest version. On an older version the button is disabled and says to switch to the latest version.

## Anchoring to a passage

An anchored note is tied to a quote on a page.

- When you click a passage, rOracle selects the text block you clicked, copies its text as the quote (up to 1000 characters) and remembers its page and position. The block is highlighted on the page with a marker in the margin, and clicking the marker jumps to the note.
- If the page has no text layer, the quote is empty and you are asked to type it to keep the anchor.
- If you paste a quote yourself without clicking, the note keeps the quote and page but has no highlight on the page. A quote needs a valid page number.
- The note card shows the quote and a **Page N** button that jumps to it.

### When a new version arrives

Notes are tied to the version they were written on. When a newer version of the paper becomes the latest, rOracle re-anchors your notes the next time the notes load. It looks for the exact quoted text in the new version:

- If found, the note moves to the new page and position.
- If not found, the note stays on the old version and is flagged **May not apply to version N**. Notes flagged this way stop appearing as community notes in search results.
- A note whose version differs from the one you are viewing shows **On version N**.

Notes with no quote (whole-document notes) move to the latest version automatically.

## Replies, votes and moderation

- **Reply** adds a comment under a note. Replies show three at a time.
- The thumbs on a note mean **Helpful** and **Not helpful**. You cannot vote on your own note. Editing the text of a note resets its votes.
- The three-dot menu depends on who you are: anyone but the author can **Report** (spam, abusive, wrong or misleading, or something else); the author can **Edit** or **Delete** (deleting a note also deletes its replies); the paper's owner can **Hide from readers** or unhide (the author and admins still see a hidden note); admins can **Remove**, which is audited.
- A note reported by three or more readers stops appearing in search results.

## Evidence on findings

A finding note has an **Evidence** panel. Choose **Add evidence** to record how a result was produced: stance (supports, refutes or inconclusive), the code repository and commit, an environment image digest, the agent or client and model, start and end times, inputs and outputs with optional checksums, parameters, and an optional description. You can attach a file (when a virus scanner is configured it is scanned, and an infected or unscannable file cannot be downloaded) or give a link with its checksum. Other readers can mark the evidence **Reproduced** or **Failed**. The finding card summarises the totals, for example "Evidence: 2 supports".

## Notes your agents suggest

Notes proposed by your agents never appear on a paper until you approve them on **Agent proposals**. Approved agent notes are marked **Agent-assisted**. You can edit the text and visibility before approving; the visibility defaults to private. See [agent proposals](handoffs-inbox.md#agent-proposals) and [agents and tokens](agents-and-tokens.md).

## Community notes

Community notes are notes visible to everyone that surface outside the reader:

- In search results, a result card shows up to two community notes for each paper, with the author, kind and page. Click one to open the paper's Notes tab on that note.
- To appear, a note must be visible to everyone, on the latest version, not flagged as not applying, not hidden or removed, not reported by three or more readers, and not downvoted heavily (its smoothed helpfulness score must be at least 0.4). Notes anchored to a page that the result shows rank first.
- Results show note text as plain text, cut to 500 characters.
- Labels apply to notes too: a note's effective label is the higher of the paper's label and its own ([labels](labels.md)).

## Related

- [Reading a paper](reader.md)
- [Sensitivity labels](labels.md)
