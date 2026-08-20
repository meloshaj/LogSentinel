# Day 3: Security Hardening & Secrets Management Runbook

This runbook outlines the required actions to secure the LogSentinel production cluster, ensuring the principles of **defense in depth** and **least privilege** are strictly enforced before handling live organizational telemetry.

---

## 🔒 Step 1: Secrets Management Externalization

Currently, for ease of deployment, `deploy/helm/logsentinel/templates/secret.yaml` base64-encodes secrets from `values.yaml`. **This is not safe for production.**

### Action Required:
Do not commit `JWT_SECRET_KEY` or `dbPass` into Git. Transition to an external secrets operator:
1. **External Secrets Operator (Recommended)**: Pulls secrets securely from AWS Secrets Manager, Azure Key Vault, or HashiCorp Vault.
2. **Bitnami Sealed Secrets**: Encrypts secrets offline using a cluster public key so they can be safely committed to the GitOps repository.

*Once implemented, remove `templates/secret.yaml` and reference the externally generated `Secret` name in your `envFrom` declarations.*

---

## 🛡️ Step 2: Database Role Separation (Least Privilege)

LogSentinel currently connects to TimescaleDB as the `logsentinel` owner. For production, split the database roles:

### Action Required:
Run the following inside your TimescaleDB instance to create a restricted application user:

```sql
-- 1. Create a restricted DML-only user
CREATE ROLE logsentinel_app WITH LOGIN PASSWORD 'strong_generated_password';

-- 2. Grant basic connection rights
GRANT CONNECT ON DATABASE logsentinel_db TO logsentinel_app;
GRANT USAGE ON SCHEMA public TO logsentinel_app;

-- 3. Grant DML (Insert/Select/Update) only — NO DDL (Drop/Alter)
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO logsentinel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO logsentinel_app;

-- 4. Ensure future tables created by the migration runner also grant these rights
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO logsentinel_app;
```

Update `values.yaml` `env.dbUser` and `env.dbPass` to use `logsentinel_app`.

---

## 🌐 Step 3: Kubernetes Network Policies

By default, Kubernetes pods can communicate freely. Enforce a zero-trust network boundary within the namespace.

### Action Required:
Apply a `NetworkPolicy` to ensure the `event-worker` and `drain-worker` are isolated. They should only be allowed to communicate with Valkey and TimescaleDB, and should accept **zero incoming traffic**.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress-workers
spec:
  podSelector:
    matchExpressions:
      - {key: app.kubernetes.io/name, operator: In, values: [logsentinel-drain-worker, logsentinel-event-worker]}
  policyTypes:
    - Ingress
  ingress: [] # Empty array denies all incoming traffic to these worker pods
```

The API gateway should only accept ingress from the NGINX controller:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-to-api
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: logsentinel-api
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
```

---

## 🔐 Step 4: Strict HTTP Headers & TLS

The Helm chart includes `cert-manager.io/cluster-issuer` for Let's Encrypt TLS. Ensure strict HTTP security headers are enforced at the Ingress controller.

### Action Required:
Ensure the following annotations are present in your `values.yaml` under `ingress.annotations`:
```yaml
ingress:
  annotations:
    nginx.ingress.kubernetes.io/configuration-snippet: |
      more_set_headers "Strict-Transport-Security: max-age=31536000; includeSubDomains";
      more_set_headers "X-Frame-Options: DENY";
      more_set_headers "X-Content-Type-Options: nosniff";
      more_set_headers "Content-Security-Policy: default-src 'self' wss:;";
```

*(Note: Ensure your NGINX ingress controller allows `configuration-snippet` or manage these globally in the NGINX ConfigMap).*

---

## ✅ Day 3 Sign-Off Checklist
- [ ] Secrets migrated to an external provider (Vault, AWS SM, SealedSecrets).
- [ ] `logsentinel_app` PostgreSQL role created and provisioned for the backend components.
- [ ] Kubernetes `NetworkPolicy` applied to isolate background workers.
- [ ] TLS Certificates issued successfully via `cert-manager` and HSTS enabled.
