# LogSentinel: 30-minute jury demonstration

This modifies the earlier working 100-log generator only to allow **one selected phase**, open the dashboard, and wait 25 seconds for async SDK batches and feature windows. It **does not** train/promote an ML model or fabricate anomalies/incidents. The source analysis already established that the demo account lacks an active model, so no actual ML predictions can be expected without a separate, verified model-training/promotion step.

## Setup in Windows PowerShell

1. Extract the ZIP and place `jury_emergency_demo.py` into `<LogSentinel repository>\demo\jury\`. Run PowerShell **from the repository root** (the folder with `sdk\python\logsentinel_logger.py`).
2. Revoke the previously exposed ingestion key if not already done, and use a new key scoped to your signed-in demo account. **Never paste it into ChatGPT or show the key-entry prompt during screen sharing.**
3. Preview with `python demo/jury/jury_emergency_demo.py --dry-run --phase all --delay 0.1`.
4. Live with `python demo/jury/jury_emergency_demo.py --phase all --interactive --open-browser --drain-seconds 25`. Paste the replacement ingestion key at the hidden prompt. Keep the signed-in website open and click sidebar pages without reloading.
5. Optional one-phase repeat, **only if desired and approved**: `python demo/jury/jury_emergency_demo.py --phase payment_outage --interactive --drain-seconds 25`. This sends exactly 25 new error/critical logs. It cannot create anomalies without an active model.

For the live presentation, pause at phase boundaries to show Logs and Overview. Error severity is demonstrated through actual log severity; it is **not** ML anomaly detection. Inspect Anomalies/Incidents only if existing real processing results are present. Explain transparently if those sections are empty. Analytics may omit CRITICAL in some charts per prior audit. The browser's session-refresh mechanism is not fully repaired; avoid hard refresh.

## Safety and truthfulness

- Sends 100 generated/queued logs for `--phase all`: 35 INFO normal, 25 WARN simulated auth failures, 16 ERROR + 9 CRITICAL simulated outage, 15 INFO recovery.
- Sends no requests to actual LogSentinel login endpoints during simulated auth failures.
- Requires existing SDK; no new Python dependencies.
- Terminal totals show **generated/queued**, **not proven accepted/persisted**. Verify live records by unique `RUN_ID` in the dashboard; SDK delivery occurs asynchronously.
- ML anomalies/tracking loops require an active tenant/owner Isolation Forest model and actual positive prediction. The demo account was verified to lack that artifact. A script-only change cannot fix that missing prerequisite or the currently isolated in-memory topology objects.
- GitHub/Microsoft OAuth repairs are separate from this generator. Sign in using the already-working account/session, do not change OAuth provider settings 30 minutes before presentation.
