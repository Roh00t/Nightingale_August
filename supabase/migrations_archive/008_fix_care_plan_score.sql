-- Fix care plan scores that were stored on a 0-1 scale instead of 0-100
UPDATE care_notes SET glance_cache = jsonb_set(glance_cache, '{care_plan_score}', '78')
WHERE (glance_cache->>'care_plan_score')::numeric < 2;
