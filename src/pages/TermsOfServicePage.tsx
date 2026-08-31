import { useNavigate } from "react-router";
import { motion } from "motion/react";
import { ArrowLeft, Shield, FileText } from "lucide-react";
import { LogSentinelLogo } from "./AuthShared";

export function TermsOfServicePage() {
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
        <div className="relative bg-gradient-to-br from-slate-900 via-slate-800 to-sky-900 px-6 sm:px-8 py-8 text-white overflow-hidden">
          {/* Decorative glow */}
          <div className="absolute -top-20 -right-20 w-60 h-60 bg-sky-400/10 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute -bottom-16 -left-16 w-48 h-48 bg-indigo-500/8 rounded-full blur-3xl pointer-events-none" />

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
              <FileText className="w-5 h-5 text-sky-300" />
            </div>
            <div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight">Terms of Service</h1>
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
          {/* Important notice */}
          <div className="mb-8 p-4 rounded-xl bg-amber-50 border border-amber-200/60">
            <p className="text-xs font-bold text-amber-800 uppercase tracking-wide mb-2 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              Important
            </p>
            <p className="text-xs text-amber-900 leading-relaxed">
              PLEASE READ THESE TERMS OF SERVICE CAREFULLY. BY CREATING AN ACCOUNT, ACCESSING OR USING THE LOGSENTINEL
              PLATFORM, CONNECTING LOG FORWARDING AGENTS (SUCH AS OPENTELEMETRY, FLUENT BIT, VECTOR, OR CUSTOM APIS), OR
              DOWNLOADING LOGSENTINEL SDKS, YOU AGREE TO BE BOUND BY THESE TERMS. IF YOU ARE AGREEING TO THESE TERMS ON
              BEHALF OF A COMPANY OR OTHER LEGAL ENTITY, YOU REPRESENT THAT YOU HAVE THE AUTHORITY TO BIND SUCH ENTITY TO
              THESE TERMS.
            </p>
          </div>

          <div className="space-y-7 text-[13px] text-slate-700 leading-relaxed">
            {/* Section 1 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">1</span>
                Acceptance of Terms
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>1.1. Contractual Relationship.</strong> These Terms of Service ("Terms") constitute a legally binding agreement between you (either an individual or the legal entity you represent, hereinafter "Customer," "you," or "your") and the operators and maintainers of LogSentinel ("LogSentinel," "we," "us," or "our").</p>
                <p><strong>1.2. Eligibility.</strong> You must be at least 18 years of age or possess legal corporate standing in your jurisdiction to establish an account or ingest logs into the platform.</p>
                <p><strong>1.3. Supplemental Terms.</strong> Specific features, such as experimental benchmarking utilities (ENABLE_BENCHMARKING_ENDPOINTS) or specialized third-party cloud storage connectors, may be governed by additional terms incorporated herein by reference.</p>
              </div>
            </section>

            {/* Section 2 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">2</span>
                Description of Services & Platform Architecture
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>2.1. The Platform.</strong> LogSentinel is an intelligent, high-throughput observability and telemetry analytics system. The Platform provides:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>High-velocity log ingestion via HTTP REST, OpenTelemetry Protocol (OTLP), and stream buffers;</li>
                  <li>Automated log message clustering, tokenization, and template parsing using Drain3 algorithms;</li>
                  <li>Unsupervised anomaly detection via sliding-window Isolation Forest machine learning models;</li>
                  <li>Real-time service topology discovery, causal dependency graphing, and blast-radius root-cause scoring;</li>
                  <li>Live telemetry broadcasting via WebSockets and real-time visualization dashboards;</li>
                  <li>Tiered data lifecycle management, including TimescaleDB hypertable storage and cold-storage archiving to Amazon S3/compatible object stores using Parquet serialization and cryptographic manifest verification.</li>
                </ul>
                <p><strong>2.2. Deployment Models.</strong> LogSentinel may be operated as a managed software-as-a-service ("SaaS") offering or self-hosted in customer-managed environments under applicable open-source license agreements (such as the Apache License 2.0). These Terms govern usage of any hosted services, cloud endpoints, enterprise instances, or centralized control planes operated by LogSentinel.</p>
              </div>
            </section>

            {/* Section 3 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">3</span>
                Accounts, Roles, and Authentication
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>3.1. Account Registration.</strong> To access the LogSentinel management console, you must register an account providing a valid email address, secure password, and organization metadata.</p>
                <p><strong>3.2. Single Sign-On (SSO) & External Identities.</strong> LogSentinel supports authentication through third-party identity providers, including Google OAuth 2.0, Microsoft Azure Entra ID (MSAL), and GitHub OAuth. When utilizing external authentication:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>You authorize LogSentinel to receive identity claims, tokens, and verified profile data from such providers;</li>
                  <li>You remain solely responsible for maintaining the security of your external identity credentials and organizational tenant settings (AZURE_TENANT_ID, AZURE_ALLOWED_TENANTS, etc.).</li>
                </ul>
                <p><strong>3.3. Machine-to-Machine (M2M) API Keys.</strong> Ingestion gateways utilize stateless API keys mapped to isolated Tenant Identifiers (INGEST_API_KEY). Customer is strictly responsible for securing and rotating API keys. Any log payload transmitted using a Customer API key shall be deemed authorized by Customer.</p>
                <p><strong>3.4. Credential Confidentiality.</strong> You are responsible for all activities occurring under your account or API credentials. Promptly notify LogSentinel of any unauthorized access or security breach.</p>
              </div>
            </section>

            {/* Section 4 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">4</span>
                Customer Log Data & Ingestion Guidelines
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>4.1. Ownership of Customer Data.</strong> Customer retains all rights, title, and interest in and to all log entries, trace metadata, payloads, and application telemetry transmitted to the Platform ("Customer Data").</p>
                <p><strong>4.2. License to LogSentinel.</strong> Customer grants LogSentinel a worldwide, non-exclusive, royalty-free license to ingest, store, parse, index, compress, serialize, and analyze Customer Data solely to the extent necessary to provide, maintain, optimize, and troubleshoot the Platform.</p>
                <p><strong>4.3. PII and Sensitive Data Sanitization.</strong></p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>LogSentinel is an infrastructure observability tool and is not intended to serve as a repository for unredacted sensitive personal data, Protected Health Information (PHI under HIPAA), or Primary Account Numbers / Cardholder Data (under PCI-DSS).</li>
                  <li>Customer represents and warrants that it has implemented client-side or forwarder-side log masking/sanitization (e.g., via Fluent Bit regex filters or OpenTelemetry transform processors) to strip passwords, plaintext credentials, credit card numbers, and government identification numbers before transmission to LogSentinel.</li>
                </ul>
              </div>
            </section>

            {/* Section 5 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">5</span>
                Acceptable Use Policy (AUP) & Prohibited Conduct
              </h2>
              <div className="space-y-2 ml-8">
                <p>You agree not to use or facilitate the use of LogSentinel to:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>5.1.</strong> Transmit log payloads containing malware, ransomware, exploit payloads, or malicious scripts designed to compromise the system or downstream consumers;</li>
                  <li><strong>5.2.</strong> Perform denial-of-service (DoS) attacks, flood ingestion queues beyond agreed throughput thresholds, or purposefully bypass rate-limiting controls;</li>
                  <li><strong>5.3.</strong> Interfere with tenant isolation boundaries, reverse engineer proprietary scoring heuristics, or attempt unauthorized privilege escalation;</li>
                  <li><strong>5.4.</strong> Ingest data obtained in violation of third-party privacy rights or applicable data protection regulations;</li>
                  <li><strong>5.5.</strong> Circumvent internal security controls, probe internal administrative endpoints (including /metrics or /readiness boundaries), or abuse benchmarking endpoints (/api/benchmarking/*) on shared infrastructure.</li>
                </ul>
              </div>
            </section>

            {/* Section 6 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">6</span>
                Machine Learning, Anomaly Detection & AI Output Disclaimer
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>6.1. Probabilistic Nature of Models.</strong> LogSentinel uses unsupervised template mining (Drain3) and Isolation Forest estimators to calculate anomaly scores and rank blast-radius dependencies.</p>
                <p><strong>6.2. No Guarantee of Detection or Accuracy.</strong> Customer expressly acknowledges and agrees that:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>Machine learning and statistical anomaly scoring are inherently probabilistic;</li>
                  <li>LogSentinel does not warrant that all system outages, security intrusions, infrastructure defects, or anomalies will be detected or predicted;</li>
                  <li>Root-cause scoring rankings represent automated mathematical correlations and must not replace professional human engineering judgment;</li>
                  <li>LogSentinel shall not be held liable for any system downtime, service interruptions, or operational damages arising from undetected anomalies or false positives.</li>
                </ul>
              </div>
            </section>

            {/* Section 7 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">7</span>
                Service Levels, Rate Limits, and Quotas
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>7.1. Rate Limits & Throttling.</strong> Ingestion endpoints apply token-bucket and concurrent batch constraints to prevent memory exhaustion and maintain stream stability (e.g., Valkey buffer capacities and worker batch thresholds). Payloads exceeding prescribed limits may return HTTP 429 (Too Many Requests) or be directed to a Dead Letter Queue (DLQ).</p>
                <p><strong>7.2. Maintenance & Updates.</strong> LogSentinel reserves the right to perform scheduled and emergency maintenance on database lifecycles, TimescaleDB continuous aggregates, and worker nodes.</p>
                <p><strong>7.3. High-Throughput Operations.</strong> Sustained throughput (e.g., 10,000+ logs/sec) is dependent on adequate infrastructure allocation, network bandwidth, and correct client-side batching configuration.</p>
              </div>
            </section>

            {/* Section 8 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">8</span>
                Data Storage Lifecycle, Archiving & Rehydration
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>8.1. Tiered Storage Architecture.</strong></p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li><strong>Hot Storage:</strong> Logs are maintained in TimescaleDB hypertables with active compression and continuous aggregates for a defined retention period (e.g., default 30 days).</li>
                  <li><strong>Cold Storage Archiving:</strong> Expired chunks are converted to compressed Apache Parquet files, validated against cryptographic SHA-256 sidecar manifests, and moved to Amazon S3 (or customer-specified object stores).</li>
                  <li><strong>Rehydration:</strong> Rehydration of cold archives back into queryable hot storage is subject to staging volume limits (ARCHIVE_STAGING_ROW_LIMIT) and processing time.</li>
                </ul>
                <p><strong>8.2. Data Eviction & Automated Purging.</strong> Upon the expiration of configured retention windows or unrecoverable cold-tier retention policies, data is irreversibly dropped via TimescaleDB chunk drop procedures (drop_chunks()).</p>
              </div>
            </section>

            {/* Section 9 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">9</span>
                Intellectual Property Rights
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>9.1. LogSentinel IP.</strong> LogSentinel, including its source code, algorithms, UI components, logos, documentation, and Isolation Forest training pipelines, is the intellectual property of LogSentinel and its licensors.</p>
                <p><strong>9.2. Open Source Code.</strong> Open-source distributions of the LogSentinel codebase are licensed under the Apache License 2.0. In the event of any conflict between these Terms and the Apache License 2.0 with respect to the raw open-source codebase, the Apache License 2.0 shall govern for that specific software.</p>
                <p><strong>9.3. Feedback.</strong> Any suggestions, enhancement requests, or feedback provided by Customer regarding LogSentinel may be utilized without restriction or compensation.</p>
              </div>
            </section>

            {/* Section 10 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">10</span>
                Warranties & Disclaimers
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>10.1. AS-IS Disclaimer.</strong> EXCEPT AS EXPRESSLY PROVIDED HEREIN, THE PLATFORM, SERVICES, AND APIS ARE PROVIDED ON AN "AS IS" AND "AS AVAILABLE" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS, IMPLIED, STATUTORY, OR OTHERWISE.</p>
                <p><strong>10.2. Exclusion of Implied Warranties.</strong> LOGSENTINEL SPECIFICALLY DISCLAIMS ALL IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT, AND UNINTERRUPTED OR ERROR-FREE OPERATION.</p>
              </div>
            </section>

            {/* Section 11 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">11</span>
                Limitation of Liability
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>11.1. Consequential Damages Waiver.</strong> TO THE MAXIMUM EXTENT PERMITTED BY LAW, IN NO EVENT SHALL LOGSENTINEL, ITS DIRECTORS, EMPLOYEES, OR AFFILIATES BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, INCLUDING LOSS OF PROFITS, LOSS OF DATA, SYSTEM DOWNTIME, COST OF PROCUREMENT OF SUBSTITUTE SERVICES, OR BUSINESS INTERRUPTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR INABILITY TO USE THE PLATFORM.</p>
                <p><strong>11.2. Cap on Liability.</strong> LOGSENTINEL'S TOTAL AGGREGATE LIABILITY ARISING FROM OR RELATED TO THESE TERMS OR THE SERVICE SHALL NOT EXCEED THE TOTAL FEES PAID BY CUSTOMER TO LOGSENTINEL IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM (OR $100.00 USD IF UTILIZING A FREE OR OPEN-SOURCE TIER).</p>
              </div>
            </section>

            {/* Section 12 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">12</span>
                Indemnification
              </h2>
              <div className="space-y-2 ml-8">
                <p>Customer agrees to defend, indemnify, and hold harmless LogSentinel, its officers, directors, employees, and agents against any third-party claims, liabilities, losses, damages, and expenses (including reasonable attorneys' fees) arising out of:</p>
                <ul className="list-disc ml-5 space-y-1 text-slate-600">
                  <li>Customer Data ingested into LogSentinel in violation of law or third-party rights;</li>
                  <li>Customer's breach of the Acceptable Use Policy;</li>
                  <li>Unsanitized PII or proprietary secrets transmitted via Customer API keys.</li>
                </ul>
              </div>
            </section>

            {/* Section 13 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">13</span>
                Termination & Suspension
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>13.1. Suspension for Security or Abuse.</strong> LogSentinel reserves the right to immediately suspend any API key, ingestion pipeline, or user account that presents an immediate threat to system stability, security, or integrity.</p>
                <p><strong>13.2. Termination by Customer.</strong> Customer may terminate their account at any time by ceasing ingestion and deleting their administrative profile.</p>
                <p><strong>13.3. Effect of Termination.</strong> Upon termination, Customer's right to access the web portal ceases. Active hot-storage chunks and pending Valkey streams will be purged in accordance with standard retention cycles.</p>
              </div>
            </section>

            {/* Section 14 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">14</span>
                Governing Law & Dispute Resolution
              </h2>
              <div className="space-y-2 ml-8">
                <p><strong>14.1. Governing Law.</strong> These Terms shall be governed and construed in accordance with the laws of the jurisdiction in which the service provider is registered, without regard to conflict of law principles.</p>
                <p><strong>14.2. Arbitration & Venue.</strong> Any controversy or claim arising out of or relating to these Terms shall be resolved by binding arbitration under standard commercial arbitration rules, or submitted to the exclusive jurisdiction of the competent courts of the governing jurisdiction.</p>
              </div>
            </section>

            {/* Section 15 */}
            <section>
              <h2 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="w-6 h-6 rounded-md bg-sky-100 text-sky-700 flex items-center justify-center text-xs font-bold flex-shrink-0">15</span>
                Modifications to Terms
              </h2>
              <div className="space-y-2 ml-8">
                <p>We reserve the right to modify these Terms at any time. When modifications are material, we will provide notice via email or within the LogSentinel web dashboard. Continued use of the Platform after the effective date of revisions constitutes acceptance of the updated Terms.</p>
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
                className="px-4 py-2 rounded-lg text-xs font-semibold text-sky-600 border border-sky-200 hover:bg-sky-50 transition-colors cursor-pointer"
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
