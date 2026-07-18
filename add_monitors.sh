#!/bin/bash
# Script to add system and docker monitors to the Home Monitor database.
# Usage: ./add_monitors.sh [password]
# The app must be running on localhost:8088.

set -e

BASE_URL="http://localhost:8088"
PASSWORD="${1:-${ACCESS_PASSWORD}}"

if [ -z "$PASSWORD" ]; then
  echo "Usage: $0 <password>"
  echo "Or set ACCESS_PASSWORD environment variable"
  exit 1
fi

echo "=== Logging in ==="
# Login and capture session cookie
LOGIN_RESP=$(curl -s -c /tmp/hm_cookies.txt -b /tmp/hm_cookies.txt \
  -X POST "$BASE_URL/login" \
  -d "password=$PASSWORD" \
  -w "\n%{http_code}" \
  -L)
HTTP_CODE=$(echo "$LOGIN_RESP" | tail -1)
echo "Login response: $HTTP_CODE"

# Get CSRF token
echo "=== Getting CSRF token ==="
CSRF_RESP=$(curl -s -b /tmp/hm_cookies.txt "$BASE_URL/api/csrf-token")
CSRF_TOKEN=$(echo "$CSRF_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null || echo "")
echo "CSRF token: ${CSRF_TOKEN:0:10}..."

if [ -z "$CSRF_TOKEN" ]; then
  echo "ERROR: Could not get CSRF token. Is the app running?"
  exit 1
fi

echo "=== Adding System monitor ==="
curl -s -b /tmp/hm_cookies.txt \
  -X POST "$BASE_URL/api/devices" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -d '{"id":"raspberry_pi","name":"Raspberry Pi","type":"system","timeout":5}' | python3 -m json.tool

echo ""
echo "=== Adding Docker monitor ==="
curl -s -b /tmp/hm_cookies.txt \
  -X POST "$BASE_URL/api/devices" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -d '{"id":"docker_portainer","name":"Docker · Portainer","type":"docker","timeout":5}' | python3 -m json.tool

echo ""
echo "=== Done! ==="

# Cleanup
rm -f /tmp/hm_cookies.txt
