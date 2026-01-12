#!/bin/bash
# Health check script for MAPtimize services

set -e

echo "=== MAPtimize Health Check ==="
echo ""

# Check Docker containers
echo "Docker containers:"
docker-compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "Docker compose not available"
echo ""

# Check backend health endpoint
echo "Backend API health:"
curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "Backend not responding"
echo ""

# Check frontend
echo "Frontend:"
curl -s -o /dev/null -w "Status: %{http_code}\n" http://localhost:3000 2>/dev/null || echo "Frontend not responding"
echo ""

# Check database connection
echo "Database (direct):"
PGPASSWORD=password psql -h localhost -U maptimize -d maptimize -c "SELECT 'connected' as status;" 2>/dev/null || echo "Database not accessible"
echo ""

echo "=== Done ==="
