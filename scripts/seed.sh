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

echo "Nightingale Family Clinic:"
CLINICIAN_ID=$(get_or_create_user "clinician@nightingale.demo" "demo-password-123")
echo "  Clinician (Dr. Sarah Chen): $CLINICIAN_ID"

STAFF_ID=$(get_or_create_user "staff@nightingale.demo" "demo-password-123")
echo "  Staff (Nurse James Rivera): $STAFF_ID"

PATIENT_ID=$(get_or_create_user "patient@nightingale.demo" "demo-password-123")
echo "  Patient (Alice Wong): $PATIENT_ID"

ADMIN_ID=$(get_or_create_user "admin@nightingale.demo" "demo-password-123")
echo "  Admin (Maria Santos): $ADMIN_ID"

echo "Sunrise Medical Center:"
S_CLINICIAN_ID=$(get_or_create_user "dr.miller@sunrise.demo" "demo-password-123")
echo "  Clinician (Dr. James Miller): $S_CLINICIAN_ID"

S_STAFF_ID=$(get_or_create_user "emma.wilson@sunrise.demo" "demo-password-123")
echo "  Staff (Emma Wilson): $S_STAFF_ID"

S_PATIENT_ID=$(get_or_create_user "robert.lee@sunrise.demo" "demo-password-123")
echo "  Patient (Robert Lee): $S_PATIENT_ID"

S_ADMIN_ID=$(get_or_create_user "michael.brown@sunrise.demo" "demo-password-123")
echo "  Admin (Michael Brown): $S_ADMIN_ID"

# All eight ids are required: seed_demo_data takes no defaults, because
# profiles.id references auth.users and a generated uuid would violate the FK.
MISSING=""
for pair in "CLINICIAN_ID:$CLINICIAN_ID" "STAFF_ID:$STAFF_ID" "PATIENT_ID:$PATIENT_ID" "ADMIN_ID:$ADMIN_ID" \
            "S_CLINICIAN_ID:$S_CLINICIAN_ID" "S_STAFF_ID:$S_STAFF_ID" "S_PATIENT_ID:$S_PATIENT_ID" "S_ADMIN_ID:$S_ADMIN_ID"; do
  name="${pair%%:*}"; val="${pair#*:}"
  [ -z "$val" ] && MISSING="$MISSING $name"
done
if [ -n "$MISSING" ]; then
  echo ""
  echo "ERROR: these users failed to resolve:$MISSING"
  exit 1
fi

echo ""
echo "Seeding database..."

# Call the seed function via Supabase REST RPC
# Argument names carry a p_ prefix (they would otherwise shadow column names
# inside the function body).
SEED_RESULT=$(curl -s "$URL/rest/v1/rpc/seed_demo_data" \
  -H "apikey: $KEY" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"p_clinician_id\": \"$CLINICIAN_ID\",
    \"p_staff_id\": \"$STAFF_ID\",
    \"p_patient_id\": \"$PATIENT_ID\",
    \"p_admin_id\": \"$ADMIN_ID\",
    \"p_sunrise_clinician_id\": \"$S_CLINICIAN_ID\",
    \"p_sunrise_staff_id\": \"$S_STAFF_ID\",
    \"p_sunrise_patient_id\": \"$S_PATIENT_ID\",
    \"p_sunrise_admin_id\": \"$S_ADMIN_ID\"
  }")

echo "Seed result: $SEED_RESULT"

echo ""
echo "Done! Demo accounts (all use password: demo-password-123)"
echo "  Nightingale Family Clinic:"
echo "    clinician@nightingale.demo   Dr. Sarah Chen"
echo "    staff@nightingale.demo       Nurse James Rivera"
echo "    patient@nightingale.demo     Alice Wong"
echo "    admin@nightingale.demo       Maria Santos"
echo "  Sunrise Medical Center:"
echo "    dr.miller@sunrise.demo       Dr. James Miller"
echo "    emma.wilson@sunrise.demo     Emma Wilson"
echo "    robert.lee@sunrise.demo      Robert Lee"
echo "    michael.brown@sunrise.demo   Michael Brown"
