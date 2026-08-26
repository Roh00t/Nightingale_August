import { createPublicKey, type KeyObject } from "node:crypto";
import jwt from "jsonwebtoken";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// ----------------------------------------------------------------
// Types
// ----------------------------------------------------------------

/** Profile fields extracted from Supabase after JWT verification. */
export interface UserProfile {
  id: string;
  role: "patient" | "staff" | "clinician" | "admin";
  clinic_id: string;
  display_name: string;
}

/** Decoded Supabase JWT payload (subset of fields we care about). */
interface SupabaseJwtPayload {
  sub: string; // auth.users id
  aud: string;
  exp: number;
  iat: number;
  role: string; // "authenticated" for logged-in users
}

// ----------------------------------------------------------------
// Supabase admin client (service role -- server-side only)
// ----------------------------------------------------------------

let _supabaseAdmin: SupabaseClient | null = null;

export function getSupabaseAdmin(): SupabaseClient {
  if (_supabaseAdmin) return _supabaseAdmin;

  const url = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !serviceKey) {
    throw new Error(
      "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables"
    );
  }

  _supabaseAdmin = createClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  return _supabaseAdmin;
}

// ----------------------------------------------------------------
// JWT verification
// ----------------------------------------------------------------

let _cachedPublicKey: KeyObject | null = null;

/**
 * Build a public key from the SUPABASE_JWT_JWK environment variable (ES256 JWK)
 * or fall back to SUPABASE_JWT_SECRET for legacy HS256 projects.
 */
function getVerificationKey(): { key: string | KeyObject; algorithms: jwt.Algorithm[] } {
  // Prefer ES256 JWK (new Supabase projects use asymmetric signing keys)
  const jwkJson = process.env.SUPABASE_JWT_JWK;
  if (jwkJson) {
    if (!_cachedPublicKey) {
      const jwk = JSON.parse(jwkJson);
      _cachedPublicKey = createPublicKey({ key: jwk, format: "jwk" });
    }
    return { key: _cachedPublicKey, algorithms: ["ES256"] };
  }

  // Fall back to HS256 symmetric secret (legacy Supabase projects)
  const secret = process.env.SUPABASE_JWT_SECRET;
  if (secret) {
    return { key: secret, algorithms: ["HS256"] };
  }

  throw new Error(
    "Missing SUPABASE_JWT_JWK or SUPABASE_JWT_SECRET environment variable"
  );
}

/**
 * Verify a Supabase-issued JWT and return the decoded payload.
 *
 * Supports both:
 * - ES256 via SUPABASE_JWT_JWK (JWK public key from Dashboard -> Settings -> API -> JWT Signing Keys)
 * - HS256 via SUPABASE_JWT_SECRET (legacy projects)
 */
export function verifyToken(token: string): SupabaseJwtPayload {
  const { key, algorithms } = getVerificationKey();

  try {
    const decoded = jwt.verify(token, key, { algorithms }) as SupabaseJwtPayload;
    return decoded;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`JWT verification failed: ${message}`);
  }
}

// ----------------------------------------------------------------
// Profile lookup
// ----------------------------------------------------------------

/**
 * Fetch the user's profile row from the `profiles` table.
 *
 * This uses the service-role client so it bypasses RLS -- intentional
 * because this runs on the trusted collaboration server.
 */
export async function fetchUserProfile(userId: string): Promise<UserProfile> {
  const supabase = getSupabaseAdmin();

  const { data, error } = await supabase
    .from("profiles")
    .select("id, role, clinic_id, display_name")
    .eq("id", userId)
    .single();

  if (error || !data) {
    throw new Error(
      `Failed to fetch profile for user ${userId}: ${error?.message ?? "not found"}`
    );
  }

  return data as UserProfile;
}

// ----------------------------------------------------------------
// Full authenticate flow
// ----------------------------------------------------------------

/**
 * End-to-end authentication: verify the JWT, look up the profile,
 * and return the enriched user context.
 */
export async function authenticateUser(token: string): Promise<UserProfile> {
  const payload = verifyToken(token);
  const profile = await fetchUserProfile(payload.sub);
  return profile;
}
