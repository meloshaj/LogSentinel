#!/bin/bash
set -e

echo "Starting LogSentinel Demo Fleet..."
echo "Stopping any existing containers..."
docker compose -f docker-compose.demo.yml down -v

echo "Building and starting the demo stack..."
docker compose -f docker-compose.demo.yml up -d --build

echo "Waiting for services to become healthy..."
sleep 15

echo "Demo fleet is running!"
echo "Dashboard: http://localhost:8080"
echo "To trigger an incident, run: python scripts/trigger_demo_incident.py"
