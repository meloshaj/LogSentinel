import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
import aiohttp
import websockets

API_URL = "http://localhost:8000/api/v1/ingest/bulk"
WS_URL = "ws://localhost:8000/ws/telemetry"
API_KEY = "ls_live_demo_key_123"

class LatencyProbe:
    def __init__(self, duration: int, probe_interval: float):
        self.duration = duration
        self.probe_interval = probe_interval
        self.latencies = []
        self.running = True
        self.sent_probes = {}

    async def emit_probes(self):
        async with aiohttp.ClientSession() as session:
            start_time = time.time()
            while time.time() - start_time < self.duration:
                probe_id = str(uuid.uuid4())
                send_time = time.time_ns()
                
                payload = {
                    "source": "e2e-probe",
                    "logs": [{
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "service_name": "latency-probe-service",
                        "level": "INFO",
                        "message": f"E2E Latency Probe {probe_id}",
                        "trace_id": probe_id,
                        "metadata": {
                            "probe_id": probe_id,
                            "send_time_ns": send_time
                        }
                    }]
                }
                
                headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
                
                self.sent_probes[probe_id] = send_time
                try:
                    async with session.post(API_URL, json=payload, headers=headers):
                        pass
                except Exception as e:
                    print(f"Error sending probe: {e}")
                    
                await asyncio.sleep(self.probe_interval)
                
            self.running = False

    async def listen_ws(self):
        start_wait_time = None
        try:
            async with websockets.connect(WS_URL) as ws:
                while self.running or self.sent_probes:
                    if not self.running and start_wait_time is None:
                        start_wait_time = time.time()
                    
                    if start_wait_time and time.time() - start_wait_time > 5.0:
                        print("Timeout waiting for remaining probes.")
                        break

                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(message)
                        
                        if data.get("type") == "frame_update":
                            for event in data.get("payload", {}).get("events", []):
                                if event.get("type") in ["log.parsed", "anomaly.detected"]:
                                    probe_id = None
                                    if event["type"] == "log.parsed":
                                        log_data = event.get("payload", {}).get("log", {})
                                        if log_data and log_data.get("trace_id") in self.sent_probes:
                                            probe_id = log_data.get("trace_id")
                                    
                                    if not probe_id:
                                        msg_str = json.dumps(event)
                                        for pid in list(self.sent_probes.keys()):
                                            if pid in msg_str:
                                                probe_id = pid
                                                break
                                                
                                    if probe_id and probe_id in self.sent_probes:
                                        recv_time = time.time_ns()
                                        send_time = self.sent_probes.pop(probe_id)
                                        latency_ms = (recv_time - send_time) / 1_000_000.0
                                        self.latencies.append(latency_ms)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            print(f"WS Error: {e}")

    async def run(self):
        print(f"Starting E2E Latency Probe for {self.duration}s (interval: {self.probe_interval}s)")
        await asyncio.gather(
            self.emit_probes(),
            self.listen_ws()
        )
        
        if not self.latencies:
            print("No probes received back via WebSocket!")
            return
            
        self.latencies.sort()
        
        print("\n--- E2E Latency Results ---")
        print(f"Probes sent/received: {len(self.latencies)}")
        print(f"P50 Latency: {self.latencies[int(len(self.latencies)*0.5)]:.2f} ms")
        print(f"P90 Latency: {self.latencies[int(len(self.latencies)*0.90)]:.2f} ms")
        print(f"P95 Latency: {self.latencies[int(len(self.latencies)*0.95)]:.2f} ms")
        print(f"P99 Latency: {self.latencies[int(len(self.latencies)*0.99)]:.2f} ms")
        print(f"Max Latency: {self.latencies[-1]:.2f} ms")
        
        # Write to JSON for automated reporting
        with open("e2e_results.json", "w") as f:
            json.dump({
                "p50": self.latencies[int(len(self.latencies)*0.5)],
                "p90": self.latencies[int(len(self.latencies)*0.90)],
                "p95": self.latencies[int(len(self.latencies)*0.95)],
                "p99": self.latencies[int(len(self.latencies)*0.99)],
                "max": self.latencies[-1]
            }, f)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    
    probe = LatencyProbe(args.duration, args.interval)
    asyncio.run(probe.run())
