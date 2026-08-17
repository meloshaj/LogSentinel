import os
import subprocess
import json
from datetime import datetime

# Test Matrix
MATRIX = [
    {"batch_size": 50, "rate": 2000, "duration": 15},
    {"batch_size": 250, "rate": 5000, "duration": 15},
    {"batch_size": 1000, "rate": 10000, "duration": 20},
]

DOCS_DIR = "docs/benchmarks"
REPORT_PATH = os.path.join(DOCS_DIR, "benchmark_results.md")

def run_test(config):
    print(f"\n==============================================")
    print(f"Running Benchmark: {config['rate']} logs/s | Batch {config['batch_size']}")
    print(f"==============================================")
    
    # Remove old results
    if os.path.exists("load_test_results.json"):
        os.remove("load_test_results.json")
    if os.path.exists("e2e_results.json"):
        os.remove("e2e_results.json")
        
    workers = min(50, max(10, int(config['rate'] / 500)))
    
    load_cmd = [
        "python", "benchmarks/load_test_ingestion.py",
        "--batch-size", str(config['batch_size']),
        "--rate", str(config['rate']),
        "--duration", str(config['duration']),
        "--workers", str(workers)
    ]
    
    e2e_cmd = [
        "python", "benchmarks/e2e_latency_probe.py",
        "--duration", str(config['duration']),
        "--interval", "1.0"
    ]
    
    # Launch E2E probe
    probe = subprocess.Popen(e2e_cmd)
    
    # Launch Load Generator
    load = subprocess.Popen(load_cmd)
    
    load.wait()
    probe.wait()
    
    results = {}
    try:
        with open("load_test_results.json") as f:
            results["load"] = json.load(f)
    except:
        results["load"] = None
        
    try:
        with open("e2e_results.json") as f:
            results["e2e"] = json.load(f)
    except:
        results["e2e"] = None
        
    return results

def generate_markdown(results_map):
    os.makedirs(DOCS_DIR, exist_ok=True)
    
    with open(REPORT_PATH, "w") as f:
        f.write("# LogSentinel Benchmark Results\n\n")
        f.write(f"**Generated At**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## Ingestion & Pipeline E2E Latency\n\n")
        
        f.write("| Target Rate | Batch Size | Actual Throughput | HTTP P95 (ms) | E2E P50 (ms) | E2E P95 (ms) | E2E P99 (ms) |\n")
        f.write("|-------------|------------|-------------------|---------------|--------------|--------------|--------------|\n")
        
        for config, res in zip(MATRIX, results_map):
            rate = config['rate']
            batch = config['batch_size']
            
            load = res.get("load")
            e2e = res.get("e2e")
            
            if load:
                throughput = f"{load['throughput']:.0f} logs/s"
                http_p95 = f"{load['p95_latency']:.1f}"
            else:
                throughput = "FAILED"
                http_p95 = "-"
                
            if e2e:
                e2e_p50 = f"{e2e['p50']:.1f}"
                e2e_p95 = f"{e2e['p95']:.1f}"
                e2e_p99 = f"{e2e['p99']:.1f}"
            else:
                e2e_p50 = "-"
                e2e_p95 = "-"
                e2e_p99 = "-"
                
            f.write(f"| {rate} logs/s | {batch} | {throughput} | {http_p95} | {e2e_p50} | {e2e_p95} | {e2e_p99} |\n")
            
        f.write("\n\n")
        f.write("> **Note**: E2E latency measures the time from the log payload leaving the load generator to the exact moment the parsed log or anomaly detection event arrives at the browser client over WebSockets.\n")
        
    print(f"\nReport successfully generated at {REPORT_PATH}")

def main():
    results = []
    for config in MATRIX:
        res = run_test(config)
        results.append(res)
        
    generate_markdown(results)

if __name__ == "__main__":
    main()
