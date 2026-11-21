# Stage 3.32F: Community publishing known issues and limits

| # | Item | Impact | Status / action |
|---|---|---|---|
| 1 | **No posts API access** (pilot limited to approved Google Cloud projects; general availability planned for 2027; no application process) | API publishing impossible | External blocker. Use the manual handoff. Re-run the read-only capability check periodically. |
| 2 | **Token lacks an insert scope** (`youtube.force-ssl` or broader) | API publishing impossible even with access | Needs your explicit authorization for a re-consent. Recommended: a separate Community token. **Not done.** |
| 3 | **The API cannot schedule posts** | — | Timed publication uses the local queue (authorized per post). Scheduling in YouTube Studio stays available through the manual handoff. |
| 4 | **The `imageUpload` request format** (`uploadType=media`) is not stated in the documentation | First real publication could fail at the image stage | A failure there is definitive or retried, never ambiguous: nothing is posted. Confirm on the first real use. |
| 5 | **Manual posts cannot be verified** | "I posted it manually" is operator-reported | Recorded as `MANUAL_REPORTED_POSTED`, shown as MANUAL_HANDOFF (completed, not verified). Never counted as PUBLISHED. |
| 6 | **Reconcile matches on text** (`posts.list`, published after the request) | An identical post published by hand could be matched | Unlikely in practice (exact text). The operator can still resolve by hand. |
| 7 | **Small edit/queue race**: an edit committing between the queue's gate check and its insert call (milliseconds) | The previously approved content could be sent | The draft is re-read and the content re-hashed immediately before sending; an edit during an in-flight row is refused. The residual window is accepted and documented. |
| 8 | **The queue runs on the 5-minute scheduler tick** | A queued post goes out up to 5 minutes after its time (requires the scheduler daemon) | By design; shown in the dashboard. |
| 9 | **Notification daily cap** (existing) | Under a failure storm, some notifications may be dropped | Events and the dashboard still show every state. |
| 10 | **Retry exhaustion** | After 3 attempts a post is FAILED | Retry explicitly or use the manual handoff. |
