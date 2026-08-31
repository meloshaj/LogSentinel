import { useNavigate } from "react-router";
import { motion } from "motion/react";
import { ArrowLeft, Shield, Lock } from "lucide-react";
import { LogSentinelLogo } from "./AuthShared";

export function PrivacyPolicyPage() {
  const navigate = useNavigate();

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.19, 1, 0.22, 1] }}
      className="w-full max-w-3xl"
    >
      <div className="bg-white rounded-2xl shadow-xl shadow-black/5 overflow-hidden">
        {/* Header */}
        <div className="relative bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 px-6 sm:px-8 py-8 text-white overflow-hidden">
          {/* Decorative glow */}
          <div className="absolute -top-20 -right-20 w-60 h-60 bg-indigo-400/10 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute -bottom-16 -left-16 w-48 h-48 bg-violet-500/8 rounded-full blur-3xl pointer-events-none" />

          <div className="relative flex items-center justify-between mb-6">
            <button
              type="button"
              onClick={() => navigate(-1)}
              className="flex items-center gap-1.5 text-xs font-medium text-slate-300 hover:text-white transition-colors cursor-pointer group"
            >
              <ArrowLeft className="w-3.5 h-3.5 group-hover:-translate-x-0.5 transition-transform" />
              Back
            </button>
            <LogSentinelLogo />
          </div>

          <div className="relative flex items-center gap-3 mb-3">
            <div className="p-2 rounded-lg bg-white/10 backdrop-blur-sm border border-white/10">
              <Lock className="w-5 h-5 text-indigo-300" />
            </div>
            <div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight">Privacy Policy</h1>
              <p className="text-xs text-slate-300 font-mono tracking-wide mt-0.5">VERSION 1.0.0</p>
            </div>
          </div>

          <div className="relative flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400 font-mono">
            <span className="flex items-center gap-1.5">
              <Shield className="w-3 h-3" />
              EFFECTIVE: AUGUST 31, 2026
            </span>
            <span className="w-px h-3 bg-white/15" />
            <span>LAST MODIFIED: AUGUST 31, 2026</span>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 sm:px-8 py-8 max-h-[65vh] overflow-y-auto legal-scroll">
          {/* Intro */}
          <div className="mb-8 p-4 rounded-xl bg-indigo-50 border border-indigo-200/60">
            <p className="text-xs text-indigo-900 leading-relaxed">
              LogSentinel ("we," "us," or "our") is dedicated to protecting the privacy of individuals whose personal
              data we collect and process. This Privacy Policy details our practices concerning the collection, use,
              disclosure, and protection of information across the LogSentinel observability platform, website, APIs, and
              SDKs.
            </p>
          </div>

          <div className="space-y-7 text-[13px] text-slate-700 leading-relaxed">
            {/* Section 1 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">1</span>
                Introduction & Scope
              </h2>
              <div className="space-y-2 ml-8">
                <p>This Privacy Policy applies to:</p>
                <ol className="list-decimal ml-5 space-y-1 text-slate-600">
                  <li>Users who access or register on the LogSentinel web console;</li>
                  <li>Organizations and developers who transmit system logs, metric streams, and application traces to LogSentinel via OTLP, Fluent Bit, Vector, or REST APIs;</li>
                  <li>Visitors to our public marketing web properties and documentation portals.</li>
                </ol>
              </div>
            </section>

            {/* Section 2 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">2</span>
                Dual Capacity: Data Controller vs. Data Processor
              </h2>
              <div className="space-y-3 ml-8">
                <p>Depending on the context of data processing, LogSentinel acts as either a Data Controller or a Data Processor:</p>
                {/* Roles table */}
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-slate-50">
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Role</th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Context</th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Data Handled</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td className="px-3 py-2 font-semibold text-sky-700 border-b border-slate-100">Data Controller</td>
                        <td className="px-3 py-2 text-slate-600 border-b border-slate-100">Console users / admins</td>
                        <td className="px-3 py-2 text-slate-600 border-b border-slate-100">Account credentials, profile, billing, direct support inquiries</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-2 font-semibold text-indigo-700">Data Processor</td>
                        <td className="px-3 py-2 text-slate-600">Customer servers / services</td>
                        <td className="px-3 py-2 text-slate-600">Raw application logs, traces and OTLP payloads processed per Customer configuration</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>LogSentinel as Data Controller:</strong> We act as a Data Controller regarding administrative user accounts, registration data, billing details, and direct support inquiries.</li>
                  <li><strong>LogSentinel as Data Processor:</strong> When our customers forward application, server, container, and network logs to LogSentinel, the Customer acts as the Data Controller, and LogSentinel acts as a Data Processor. We process such data exclusively according to Customer's configuration and instruction.</li>
                </ul>
              </div>
            </section>

            {/* Section 3 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">3</span>
                Information We Collect
              </h2>
              <div className="space-y-3 ml-8">
                <p><strong>3.1. Account & Identity Information (Collected as Controller)</strong></p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>Registration Credentials:</strong> Email address, bcrypt-hashed passwords, full name, organization title.</li>
                  <li><strong>External Single Sign-On (SSO) Claims:</strong>
                    <ul className="list-disc ml-5 mt-1 space-y-1">
                      <li><strong>Google OAuth:</strong> Email address, name, profile image, verified user ID.</li>
                      <li><strong>Microsoft Azure Entra ID (MSAL):</strong> Tenant ID (tid), Object ID (oid), User Principal Name (UPN), assigned roles/scopes (access_as_user).</li>
                      <li><strong>GitHub OAuth:</strong> GitHub username, email, public profile avatar.</li>
                    </ul>
                  </li>
                  <li><strong>Audit & Session Logs:</strong> IP address, browser user-agent, session timestamps, and authentication audit records.</li>
                </ul>

                <p><strong>3.2. Customer Telemetry & Log Data (Processed as Processor)</strong></p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>Log Payloads:</strong> Structured JSON or unformatted text containing timestamps, log severity levels (INFO, WARN, ERROR), message strings, process IDs, and stack traces.</li>
                  <li><strong>Network & Service Attributes:</strong> Hostnames, container IDs, Kubernetes namespaces, microservice node names, and HTTP trace identifiers.</li>
                  <li><strong>Drain3 Parsed Structures:</strong> Algorithmic log template tokens (e.g., <code className="bg-slate-100 px-1 py-0.5 rounded text-[11px]">Failed password for &lt;*&gt; from &lt;*&gt; port &lt;*&gt;</code>).</li>
                </ul>

                <p><strong>3.3. Usage & System Performance Metrics</strong></p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>API consumption statistics, batch ingestion latency, WebSocket active connection states, and worker queue health metrics.</li>
                </ul>
              </div>
            </section>

            {/* Section 4 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">4</span>
                Legal Bases for Processing (GDPR Article 6)
              </h2>
              <div className="space-y-2 ml-8">
                <p>When operating under the General Data Protection Regulation (GDPR), we process personal data under the following legal bases:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>Performance of a Contract (Art. 6(1)(b)):</strong> Providing authentication, log ingestion, anomaly scoring, and alerting services requested by Customer.</li>
                  <li><strong>Legitimate Interests (Art. 6(1)(f)):</strong> Preventing fraud, securing APIs against brute-force attacks, optimizing ML parser performance, and ensuring infrastructure reliability.</li>
                  <li><strong>Compliance with Legal Obligations (Art. 6(1)(c)):</strong> Retaining transactional or administrative records for statutory taxation and corporate compliance.</li>
                  <li><strong>Consent (Art. 6(1)(a)):</strong> When you explicitly authenticate via third-party OAuth providers or opt into communications.</li>
                </ul>
              </div>
            </section>

            {/* Section 5 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">5</span>
                How We Use Information
              </h2>
              <div className="space-y-2 ml-8">
                <p>We use collected information to:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>5.1. Deliver Observability Services:</strong> Ingest, parse, index, and visualize log events in real-time;</li>
                  <li><strong>5.2. Execute Machine Learning Workflows:</strong> Train and evaluate unsupervised Isolation Forest models on log feature vectors to compute blast-radius and anomaly scores;</li>
                  <li><strong>5.3. Real-Time Notification:</strong> Broadcast system failure alerts, incident trees, and dependency topology maps via authenticated WebSockets;</li>
                  <li><strong>5.4. Enforce Security & Rate Limits:</strong> Prevent credential abuse, validate M2M API keys against tenant boundaries, and protect internal API surfaces;</li>
                  <li><strong>5.5. Deliver Transactional Emails:</strong> Send password reset links, verification codes, and incident alerts via configured SMTP relays (SMTPSettings).</li>
                </ul>
              </div>
            </section>

            {/* Section 6 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">6</span>
                Third-Party Integrations & Identity Providers
              </h2>
              <div className="space-y-3 ml-8">
                <p>LogSentinel integrates with trusted third-party service providers:</p>
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-slate-50">
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Provider</th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Purpose</th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Data Exchanged</th>
                        <th className="px-3 py-2 text-left font-bold text-slate-700 border-b border-slate-200">Transfer Safeguards</th>
                      </tr>
                    </thead>
                    <tbody className="text-slate-600">
                      <tr className="border-b border-slate-100">
                        <td className="px-3 py-2 font-medium text-slate-700">Google Identity (OAuth 2.0)</td>
                        <td className="px-3 py-2">SSO Authentication</td>
                        <td className="px-3 py-2">Identity token, email, name</td>
                        <td className="px-3 py-2">Google API Terms / SCCs</td>
                      </tr>
                      <tr className="border-b border-slate-100">
                        <td className="px-3 py-2 font-medium text-slate-700">Microsoft Azure Entra ID</td>
                        <td className="px-3 py-2">Enterprise SSO / MSAL</td>
                        <td className="px-3 py-2">Access token, OIDC claims, Tenant ID</td>
                        <td className="px-3 py-2">Microsoft Enterprise Agreement / SCCs</td>
                      </tr>
                      <tr className="border-b border-slate-100">
                        <td className="px-3 py-2 font-medium text-slate-700">GitHub OAuth</td>
                        <td className="px-3 py-2">Developer SSO</td>
                        <td className="px-3 py-2">GitHub profile, primary email</td>
                        <td className="px-3 py-2">GitHub Privacy Agreement</td>
                      </tr>
                      <tr className="border-b border-slate-100">
                        <td className="px-3 py-2 font-medium text-slate-700">AWS / S3</td>
                        <td className="px-3 py-2">Cold Storage Archiving</td>
                        <td className="px-3 py-2">Encrypted Parquet files, SHA-256 manifests</td>
                        <td className="px-3 py-2">AWS Data Processing Addendum</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-2 font-medium text-slate-700">SMTP Relays</td>
                        <td className="px-3 py-2">Transactional Emails</td>
                        <td className="px-3 py-2">Destination email, reset tokens</td>
                        <td className="px-3 py-2">TLS-encrypted SMTP delivery</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            {/* Section 7 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">7</span>
                Data Security & Cryptographic Safeguards
              </h2>
              <div className="space-y-2 ml-8">
                <p>LogSentinel implements comprehensive technical and organizational measures (TOMs) to safeguard data:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>7.1. Encryption in Transit:</strong> All external traffic to the API gateway and web UI is routed through a TLS-terminating reverse proxy (Caddy) enforcing modern cipher suites (TLS 1.2/1.3) and automatic HTTPS certificate provisioning.</li>
                  <li><strong>7.2. Encryption at Rest:</strong>
                    <ul className="list-disc ml-5 mt-1 space-y-1">
                      <li>Database tables and continuous aggregates in TimescaleDB/PostgreSQL leverage storage volume encryption and application-level encryption keys (ENCRYPTION_KEY).</li>
                      <li>Cold storage Parquet archives on Amazon S3 utilize server-side encryption (SSE-S3 or SSE-KMS).</li>
                    </ul>
                  </li>
                  <li><strong>7.3. Cryptographic Manifest Verification:</strong> Cold storage archives generate sidecar JSON manifests containing SHA-256 hashes for each serialized Parquet chunk. During rehydration, hashes are re-calculated to verify mathematical integrity and detect tampering.</li>
                  <li><strong>7.4. Tenant Isolation:</strong> Multi-tenant architecture guarantees that log queries, feature vectors, and anomaly alerts are strictly segregated by tenant_id at both the database and streaming buffer layers.</li>
                  <li><strong>7.5. Access Controls & Shielded Routes:</strong> Diagnostic endpoints (/metrics, /readiness, /openapi.json) are blocked from public ingress at the reverse proxy layer.</li>
                </ul>
              </div>
            </section>

            {/* Section 8 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">8</span>
                Data Retention, Hot/Cold Tiering & Deletion
              </h2>
              <div className="space-y-3 ml-8">
                {/* Lifecycle visualization */}
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 font-mono p-3 rounded-lg bg-slate-50 border border-slate-200">
                  <span className="px-2 py-1 rounded bg-sky-100 text-sky-700 font-bold">1. Ingestion</span>
                  <span>→</span>
                  <span className="px-2 py-1 rounded bg-amber-100 text-amber-700 font-bold">2. Hot Storage (0-30 days)</span>
                  <span>→</span>
                  <span className="px-2 py-1 rounded bg-blue-100 text-blue-700 font-bold">3. Cold Archive (S3)</span>
                  <span>→</span>
                  <span className="px-2 py-1 rounded bg-red-100 text-red-700 font-bold">4. Deletion / Rehydration</span>
                </div>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>8.1. Hot Storage Window:</strong> Raw logs and compressed hypertable chunks remain queryable in the active database for the duration configured by the administrator (e.g., ARCHIVE_HOT_RETENTION_DAYS, standard 30 days).</li>
                  <li><strong>8.2. Cold Storage Archival:</strong> After the hot retention window elapses, the automated archive worker serializes data into Parquet format, verifies checksums, writes to the S3 bucket, and safely drops the database chunk.</li>
                  <li><strong>8.3. Permanent Deletion:</strong> When a Customer account is closed, or when cold retention policies expire, files are permanently deleted from object storage without capability of recovery.</li>
                </ul>
              </div>
            </section>

            {/* Section 9 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">9</span>
                International Data Transfers
              </h2>
              <div className="space-y-2 ml-8">
                <p>When data is transferred across international borders (including to cloud storage facilities in the United States or European Union), we ensure compliance through:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>Standard Contractual Clauses (SCCs) approved by the European Commission;</li>
                  <li>Choice of geographic data storage regions (S3_REGION, European data residency options);</li>
                  <li>Cryptographic protection ensuring data is inaccessible during transit.</li>
                </ul>
              </div>
            </section>

            {/* Section 10 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">10</span>
                Data Subject Rights (GDPR / CCPA / CPRA)
              </h2>
              <div className="space-y-2 ml-8">
                <p>Depending on your residency, you may exercise specific statutory rights regarding personal data for which LogSentinel is the Data Controller:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>Right of Access (Art. 15 GDPR):</strong> Request confirmation of whether we process your personal data and obtain a copy.</li>
                  <li><strong>Right to Rectification (Art. 16 GDPR):</strong> Correct inaccurate or incomplete profile information.</li>
                  <li><strong>Right to Erasure / "Right to be Forgotten" (Art. 17 GDPR):</strong> Request permanent deletion of your user account and profile data.</li>
                  <li><strong>Right to Restrict Processing (Art. 18 GDPR):</strong> Restrict processing under certain contested circumstances.</li>
                  <li><strong>Right to Data Portability (Art. 20 GDPR):</strong> Receive your personal data in a structured, commonly used, machine-readable format.</li>
                  <li><strong>Right to Object (Art. 21 GDPR):</strong> Object to processing founded upon legitimate interest grounds.</li>
                  <li><strong>California Privacy Rights (CCPA/CPRA):</strong>
                    <ul className="list-disc ml-5 mt-1 space-y-1">
                      <li>Right to know what personal categories of data are collected;</li>
                      <li>We do not sell or share personal information for cross-context behavioral advertising.</li>
                    </ul>
                  </li>
                </ul>

                <div className="mt-3 p-3 rounded-lg bg-blue-50 border border-blue-200/60">
                  <p className="text-xs font-bold text-blue-800 uppercase tracking-wide mb-1 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                    Notice Regarding Ingested Log Data
                  </p>
                  <p className="text-xs text-blue-900 leading-relaxed">
                    If your personal data appears within application logs forwarded to LogSentinel by one of our enterprise
                    customers, please direct your data subject request directly to the relevant Customer (the Data Controller).
                    We will assist our Customers in fulfilling verified requests in accordance with our Data Processing Agreement.
                  </p>
                </div>
              </div>
            </section>

            {/* Section 11 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">11</span>
                Security Breach Notification
              </h2>
              <div className="space-y-2 ml-8">
                <p>In the event of a confirmed security incident impacting personal data under our control, LogSentinel will notify affected administrators without undue delay (and within 72 hours where required by applicable law) and provide relevant details regarding the scope and remedial actions taken.</p>
              </div>
            </section>

            {/* Section 12 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">12</span>
                Children's Privacy
              </h2>
              <div className="space-y-2 ml-8">
                <p>LogSentinel is an enterprise developer and IT operations platform. We do not knowingly collect personal data from individuals under the age of 16. If we become aware of inadvertent collection of data from a minor, we will promptly delete it.</p>
              </div>
            </section>

            {/* Section 13 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center text-xs font-bold flex-shrink-0">13</span>
                Contact Information & Data Protection Office
              </h2>
              <div className="space-y-2 ml-8">
                <p>For inquiries, data protection requests, or to exercise your rights under this Privacy Policy, please contact our privacy compliance team:</p>
                <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs font-mono text-slate-600 space-y-1">
                  <p className="font-semibold text-slate-700">LogSentinel Compliance & Security Office</p>
                  <p>Email: privacy@logsentinel.local / security@logsentinel.local</p>
                  <p>Administrative Notifications: Managed via the LogSentinel Organization Settings Console.</p>
                </div>
              </div>
            </section>
          </div>

          {/* Footer contact */}
          <div className="mt-8 pt-6 border-t border-slate-200">
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
              <div className="text-xs text-slate-500 font-mono">
                <p className="font-semibold text-slate-600 mb-1">LogSentinel Compliance & Security Office</p>
                <p>privacy@logsentinel.local · security@logsentinel.local</p>
              </div>
              <button
                type="button"
                onClick={() => navigate(-1)}
                className="px-4 py-2 rounded-lg text-xs font-semibold text-indigo-600 border border-indigo-200 hover:bg-indigo-50 transition-colors cursor-pointer"
              >
                ← Back to Registration
              </button>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
