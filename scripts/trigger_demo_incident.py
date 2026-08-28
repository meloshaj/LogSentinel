import time
import urllib.request
import urllib.error
import sys

def inject_fault(port, service_name, fault_type):
    url = f"http://localhost:{port}/fault/inject?type={fault_type}"
    print(f"[{time.strftime('%X')}] Triggering {fault_type} on {service_name} (port {port})...")
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print(f"  -> Success: {service_name} is now degraded.")
            else:
                print(f"  -> Warning: Received status {response.status}")
    except urllib.error.URLError as e:
        print(f"  -> Error connecting to {service_name}: {e}")
        sys.exit(1)

def main():
    print("==========================================")
    print(" LogSentinel Cascading Outage Simulator")
    print("==========================================\n")
    
    # T+0s: Payment Gateway DB pool lock
    inject_fault(9003, "payment-gateway", "db_pool_exhaustion")
    
    print("\nWaiting 5 seconds for upstream impact...")
    time.sleep(5)
    
    # T+5s: Order Service downstream timeout
    inject_fault(9002, "order-service", "downstream_timeout")
    
    print("\nWaiting 5 seconds for gateway cascade...")
    time.sleep(5)
    
    # T+10s: Auth Service / API Gateway 504 Timeouts
    inject_fault(9001, "auth-service", "gateway_timeout")
    
    print("\n==========================================")
    print(" Incident fully triggered!")
    print(" Check the LogSentinel Incident Timeline.")
    print("==========================================\n")
    print("To recover the fleet, you can POST to /fault/clear on ports 9001, 9002, 9003.")

if __name__ == "__main__":
    main()
