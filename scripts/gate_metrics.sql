-- Gate metrics for checkpoint C1 and Gate G (hand-off section 9). PostgreSQL only. READ ONLY: this file only selects.
-- Parameters (all timestamptz, UTC): :cohort_start  :as_of  :cohort_end
--   gate mode: cohort_end = as_of - 28 days          C1 mode: cohort_end = as_of
-- Run with the session time zone set to UTC (gate_report.py does this). Every row of `expenses` counts, soft-deleted
-- ones included (the user did create them). Admin users are left out of every metric. "Local day" is the day in the
-- user's own time zone (a bad zone name falls back to Asia/Karachi, like the app does).
WITH p AS (
  SELECT CAST(:cohort_start AS timestamptz) AS cohort_start,
         CAST(:as_of        AS timestamptz) AS as_of,
         CAST(:cohort_end   AS timestamptz) AS cohort_end
),
u AS (
  SELECT id, created_at,
         CASE WHEN timezone IN (SELECT name FROM pg_timezone_names) THEN timezone ELSE 'Asia/Karachi' END AS tz
  FROM users
  WHERE NOT is_admin
),
e AS (
  SELECT x.id, x.user_id, x.created_at, x.source,
         CAST((x.created_at AT TIME ZONE u.tz) AS date) AS local_day
  FROM expenses x
  JOIN u ON u.id = x.user_id
  CROSS JOIN p
  WHERE x.created_at < p.as_of
),
first_exp AS (
  SELECT user_id, MIN(created_at) AS first_at FROM e GROUP BY user_id
),
-- Starters: the clock starts at the FIRST expense, not at signup.
starters AS (
  SELECT f.user_id, f.first_at
  FROM first_exp f CROSS JOIN p
  WHERE f.first_at >= p.cohort_start AND f.first_at < p.cohort_end
),
judgeable AS (
  SELECT s.user_id, s.first_at
  FROM starters s CROSS JOIN p
  WHERE s.first_at <= p.as_of - interval '7 days'
),
-- Activated: 3+ expenses in the 7 days from the first one, on 2+ different local days.
activated AS (
  SELECT j.user_id, j.first_at
  FROM judgeable j
  JOIN e ON e.user_id = j.user_id
        AND e.created_at >= j.first_at
        AND e.created_at <  j.first_at + interval '7 days'
  GROUP BY j.user_id, j.first_at
  HAVING COUNT(*) >= 3 AND COUNT(DISTINCT e.local_day) >= 2
),
-- Week-4 retention: of the activated starters whose first expense is 28+ days old, 1+ expense in days 21 to 28.
retention AS (
  SELECT a.user_id,
         EXISTS (SELECT 1 FROM e
                 WHERE e.user_id = a.user_id
                   AND e.created_at >= a.first_at + interval '21 days'
                   AND e.created_at <  a.first_at + interval '28 days') AS retained
  FROM activated a CROSS JOIN p
  WHERE a.first_at <= p.as_of - interval '28 days'
),
-- AI adoption: of the users who created any expense in the last 14 days, those who created one through ai_chat or whatsapp.
last14 AS (
  SELECT e.user_id, BOOL_OR(e.source IN ('ai_chat', 'whatsapp')) AS used_ai
  FROM e CROSS JOIN p
  WHERE e.created_at >= p.as_of - interval '14 days' AND e.created_at < p.as_of
  GROUP BY e.user_id
),
-- AI correction: AI-created entries that are 24 hours to 30 days old, and which of them were edited or deleted within 24 h.
ai_entries AS (
  SELECT e.id, e.user_id, e.created_at
  FROM e CROSS JOIN p
  WHERE e.source IN ('ai_chat', 'whatsapp')
    AND e.created_at >  p.as_of - interval '30 days'
    AND e.created_at <= p.as_of - interval '24 hours'
),
corrected AS (
  SELECT a.id
  FROM ai_entries a CROSS JOIN p
  WHERE EXISTS (SELECT 1 FROM audit_log l
                WHERE l.entity_type = 'expense'
                  AND l.entity_id   = CAST(a.id AS text)
                  AND l.user_id     = a.user_id
                  AND l.action IN ('expense.update', 'expense.delete')
                  AND l.created_at >= a.created_at
                  AND l.created_at <  a.created_at + interval '24 hours'
                  AND l.created_at <  p.as_of)
),
interest AS (
  SELECT COUNT(DISTINCT l.user_id) AS n
  FROM audit_log l
  JOIN u ON u.id = l.user_id
  CROSS JOIN p
  WHERE l.action = 'upgrade_interest' AND l.created_at < p.as_of
),
-- Informational: signup to first expense, whole minutes, the LOWER median (integers only, so no rounding questions).
signup_gap AS (
  SELECT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY CAST(FLOOR(EXTRACT(EPOCH FROM (s.first_at - u.created_at)) / 60) AS bigint)) AS m
  FROM starters s
  JOIN u ON u.id = s.user_id
)
SELECT
  (SELECT COUNT(*) FROM starters)                       AS starters,
  (SELECT COUNT(*) FROM judgeable)                      AS starters_judgeable,
  (SELECT COUNT(*) FROM activated)                      AS activated,
  (SELECT COUNT(*) FROM retention)                      AS retention_base,
  (SELECT COUNT(*) FROM retention WHERE retained)       AS retained,
  (SELECT COUNT(*) FROM last14)                         AS active_users_14d,
  (SELECT COUNT(*) FROM last14 WHERE used_ai)           AS ai_users_14d,
  (SELECT COUNT(*) FROM ai_entries)                     AS ai_entries_eligible,
  (SELECT COUNT(*) FROM corrected)                      AS ai_entries_corrected,
  (SELECT n FROM interest)                              AS upgrade_interest_users,
  (SELECT m FROM signup_gap)                            AS signup_to_first_entry_median_minutes;
