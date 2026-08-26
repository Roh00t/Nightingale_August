#!/bin/bash
# Creates demo auth users and seeds the database
# Usage: ./scripts/seed.sh

set -euo pipefail

# Load .env
source .env

URL="$NEXT_PUBLIC_SUPABASE_URL"
KEY="$SUPABASE_SERVICE_ROLE_KEY"

get_or_create_user() {
  local email="$1"
  local password="$2"
  local response

  # Try to create the user
  response=$(curl -s "$URL/auth/v1/admin/users" \
    -H "apikey: $KEY" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"email\": \"$email\",
      \"password\": \"$password\",
      \"email_confirm\": true
    }")

  # Extract ID from creation response
  local id
  id=$(echo "$response" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || true)

  if [ -n "$id" ]; then
    echo "$id"
    return
  fi

  # User already exists — look up by email
  response=$(curl -s "$URL/auth/v1/admin/users?per_page=50" \
    -H "apikey: $KEY" \
    -H "Authorization: Bearer $KEY")

  id=$(echo "$response" | python3 -c "
import sys, json
users = json.load(sys.stdin).get('users', json.load(open('/dev/stdin')) if False else [])
for u in (users if isinstance(users, list) else []):
    if u.get('email') == '$email':
        print(u['id'])
        break
" 2>/dev/null || true)

  if [ -z "$id" ]; then
    # Simpler fallback: parse the full response for this email
    id=$(echo "$response" | python3 -c "
import sys, json
data = json.load(sys.stdin)
users = data if isinstance(data, list) else data.get('users', [])
for u in users:
    if u.get('email') == '$email':
        print(u['id'])
        break
" 2>/dev/null || true)
  fi

  echo "$id"
}

# Clean up test probe user if it exists
echo "Cleaning up any test users..."
PROBE_RESPONSE=$(curl -s "$URL/auth/v1/admin/users?per_page=50" \
  -H "apikey: $KEY" \
  -H "Authorization: Bearer $KEY")
PROBE_ID=$(echo "$PROBE_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
users = data if isinstance(data, list) else data.get('users', [])
for u in users:
    if u.get('email') == 'test-probe@nightingale.demo':
        print(u['id'])
        break
" 2>/dev/null || true)
if [ -n "$PROBE_ID" ]; then
  curl -s -X DELETE "$URL/auth/v1/admin/users/$PROBE_ID" \
    -H "apikey: $KEY" \
    -H "Authorization: Bearer $KEY" > /dev/null
  echo "  Removed test-probe user"
fi

echo "Creating/finding demo users..."

CLINICIAN_ID=$(get_or_create_user "clinician@nightingale.demo" "demo-password-123")
echo "  Clinician (Dr. Sarah Chen): $CLINICIAN_ID"

STAFF_ID=$(get_or_create_user "staff@nightingale.demo" "demo-password-123")
echo "  Staff (Nurse James Rivera): $STAFF_ID"

PATIENT_ID=$(get_or_create_user "patient@nightingale.demo" "demo-password-123")
echo "  Patient (Alice Wong): $PATIENT_ID"

ADMIN_ID=$(get_or_create_user "admin@nightingale.demo" "demo-password-123")
echo "  Admin (Maria Santos): $ADMIN_ID"

if [ -z "$CLINICIAN_ID" ] || [ -z "$STAFF_ID" ] || [ -z "$PATIENT_ID" ] || [ -z "$ADMIN_ID" ]; then
  echo ""
  echo "ERROR: One or more users failed to resolve. Check output above."
  echo "  CLINICIAN_ID=$CLINICIAN_ID"
  echo "  STAFF_ID=$STAFF_ID"
  echo "  PATIENT_ID=$PATIENT_ID"
  echo "  ADMIN_ID=$ADMIN_ID"
  exit 1
fi

echo ""
echo "Seeding database..."

# Call the seed function via Supabase REST RPC
SEED_RESULT=$(curl -s "$URL/rest/v1/rpc/seed_demo_data" \
  -H "apikey: $KEY" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"clinician_id\": \"$CLINICIAN_ID\",
    \"staff_id\": \"$STAFF_ID\",
    \"patient_id\": \"$PATIENT_ID\",
    \"admin_id\": \"$ADMIN_ID\"
  }")

echo "Seed result: $SEED_RESULT"

echo ""
echo "Done! Demo accounts:"
echo "  clinician@nightingale.demo / demo-password-123"
echo "  staff@nightingale.demo     / demo-password-123"
echo "  patient@nightingale.demo   / demo-password-123"
echo "  admin@nightingale.demo     / demo-password-123"
