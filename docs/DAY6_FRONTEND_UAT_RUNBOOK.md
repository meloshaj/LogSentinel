# Day 6: Frontend Polish, Cross-Browser / UX & Telemetry Verification Runbook

This runbook outlines the required actions to validate the React-based LogSentinel UI under real-world conditions, focusing on WebSocket resilience, UI performance under heavy event loads, and User Acceptance Testing (UAT).

---

## 🔌 Step 1: WebSocket Resilience & Network Drops

The UI relies heavily on a stateful WebSocket connection to `/ws/telemetry` to stream live anomaly events and blast radius topology updates. It must gracefully handle dirty network conditions.

### Action Required: Network Throttling & Disconnect Drill
1. Open the LogSentinel Dashboard in Google Chrome / Edge.
2. Open **Developer Tools (F12)** -> **Network Tab**.
3. Change the network throttling profile to **Offline** for 10 seconds, then switch it back to **No Throttling**.
4. **Validation Checklist:**
   - [ ] The UI displays a "Reconnecting..." indicator when offline.
   - [ ] The WebSocket automatically re-establishes a connection (using exponential backoff).
   - [ ] No page refresh is required to resume the live stream.
   - [ ] Previous anomaly/topology state remains intact on the screen without wiping the graph.

---

## ⚡ Step 2: High-Event Rendering Optimization (Stress Test)

When a cascading failure occurs, the backend will blast hundreds of graph nodes, edges, and anomaly alerts per second over the socket. The UI must not lock up or crash the browser tab.

### Action Required: Client-Side Stress Test
1. While the LogSentinel dashboard is open, trigger a massive burst of anomalies from the terminal:
   ```bash
   python scripts/simulate_incident.py --steady-duration 5 --incident-duration 30 --rate 20
   ```
2. **Validation Checklist:**
   - [ ] The React UI remains responsive (buttons can be clicked, tabs switched).
   - [ ] The D3/React Flow dependency graph batches node rendering smoothly (e.g., via `requestAnimationFrame` or debouncing) without causing massive frame drops.
   - [ ] The browser tab memory does not grow unbounded (ensure older log items are pruned from the DOM view, keeping only the most recent N items).

---

## 🛡️ Step 3: Content Security Policy (CSP) & Cross-Browser Audit

On Day 3, we implemented strict HTTP headers at the Kubernetes Ingress layer. We must ensure these headers do not accidentally break the frontend assets.

### Action Required: Console Audit
1. Navigate to the LogSentinel dashboard.
2. Check the **Browser Console** for any red `Content Security Policy` violation errors.
3. **Validation Checklist:**
   - [ ] No `Refused to execute inline script` or `Refused to load font/image` errors exist.
   - [ ] Ensure the Vite dev server (HMR) or external CDNs are properly allowlisted if strictly required, though the production build should package all assets locally.
   - [ ] Validate core views (Logs Explorer, Blast Radius Graph, Dashboard) on the latest versions of **Chrome**, **Firefox**, and **Safari**.

---

## 🧑‍💻 Step 4: End-to-End User Acceptance Test (UAT)

Walk through the complete operator lifecycle to ensure the UX is intuitive and functional during a simulated fire-drill.

### Scenario: The 3AM Page
1. **Trigger:** Receive the simulated PagerDuty/Slack alert.
2. **Action:** Click the link in the alert to open the LogSentinel Blast Radius view.
3. **Validation:**
   - Does the graph immediately highlight the root-cause service in **Red**?
   - Can the operator click the root-cause node to open the side-panel and view the specific anomalous raw log (e.g., "Connection Refused")?
   - Are the downstream impacted services accurately highlighted in **Yellow/Orange**?

---

## ✅ Day 6 Sign-Off Checklist
- [ ] WebSocket automatic reconnection verified without page reloads.
- [ ] UI remains responsive during a 5000-event anomaly burst.
- [ ] Zero Content Security Policy (CSP) violations in the production build.
- [ ] Cross-browser UAT successfully completed (Chrome, Firefox, Safari).
