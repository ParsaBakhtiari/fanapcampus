#!/usr/bin/env bash
# End-to-end check of the whole flow through the public entry point.
#   BASE_URL=https://shop.example.com ./scripts/smoke-test.sh
#   BASE_URL=http://localhost:8080   ./scripts/smoke-test.sh      (docker compose)
# Needs: curl, python3. Creates a throw-away customer and one order (then cancels it).
set -euo pipefail
BASE_URL="${BASE_URL:-http://localhost:8080}"
EMAIL="smoke-$(date +%s)@example.com"
PASS="smoke-password-123"
json() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
step() { printf '\n== %s\n' "$*"; }

step "frontend health";  curl -fsS "$BASE_URL/healthz"
step "register";         curl -fsS -X POST "$BASE_URL/api/v1/auth/register" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" >/dev/null && echo ok
step "login"
TOKEN=$(curl -fsS -X POST "$BASE_URL/api/v1/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | json "['access_token']")
AUTH="Authorization: Bearer $TOKEN"; echo ok

step "catalog (RDS + DCS cache)"
PRODUCT=$(curl -fsS "$BASE_URL/api/v1/products" | json "[0]['id']")
curl -fsS -o /dev/null -D - "$BASE_URL/api/v1/products" | grep -i x-cache || true

step "cart (DCS)"
curl -fsS -X PUT "$BASE_URL/api/v1/cart" -H "$AUTH" -H 'Content-Type: application/json' -d "{\"product_id\":\"$PRODUCT\",\"quantity\":1}" | json "['total']"

step "checkout (Backend API -> Order Service -> RDS + OBS invoice)"
ORDER=$(curl -fsS -X POST "$BASE_URL/api/v1/checkout" -H "$AUTH" -H 'Content-Type: application/json' -H "Idempotency-Key: smoke-$(date +%s%N)" -d '{"shipping_address":"1 Test Street, Tehran"}')
ORDER_ID=$(echo "$ORDER" | json "['id']"); echo "$ORDER" | json "['number']"

step "invoice PDF (OBS)"
curl -fsS "$BASE_URL/api/v1/orders/$ORDER_ID/invoice" -H "$AUTH" -o /tmp/smoke-invoice.pdf && head -c 8 /tmp/smoke-invoice.pdf && echo

step "cancel order (restocks)"
curl -fsS -X POST "$BASE_URL/api/v1/orders/$ORDER_ID/cancel" -H "$AUTH" | json "['status']"

printf '\nSMOKE TEST PASSED\n'
