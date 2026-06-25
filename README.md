# LogSentinel
LogSentinel is an automated, real-time log anomaly detection and root-cause ranking platform. Engineered to bypass manual threshold tuning and static alerting parameters, it continuously ingests raw log data, groups similar messages into structured templates using lightweight unsupervised machine learning, and flags system deviations dynamically.

## Docker

Build and run the production container:

```bash
docker compose up --build
```

The app will be available at `http://localhost:8080`.

Build the image directly:

```bash
docker build -t logsentinel-dashboard .
docker run --rm -p 8080:80 logsentinel-dashboard
```
