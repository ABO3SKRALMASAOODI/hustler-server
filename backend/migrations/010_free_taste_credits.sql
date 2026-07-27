-- 010 — the free taste: every account without a plan holds 50 spendable credits.
--
-- Round 49 drained free balances to 0 because a free account could not spend a
-- credit anyway (editing required a plan), so the number was a promise the
-- product refused. Round 50 keeps the number and removes the refusal:
-- backend/plan_gate.py now opens the gate for exactly as long as this pool
-- lasts, so 50 credits buys a few real agent turns on the user's own footage.
--
-- This is a DATA migration, not a schema one, and it exists so "all users"
-- means every account that already signed up — not only the ones who arrive
-- after the deploy (routes/auth.py and routes/google_auth.py grant those).
--
-- Idempotent by design: it tops the bonus pool UP TO 50 rather than adding 50,
-- so re-running it never stacks a second grant onto somebody who has already
-- spent part of theirs... with one deliberate consequence — an account that
-- has spent down to, say, 12 will be topped back to 50 if this is run again.
-- Run it ONCE. The `< 50` guard keeps a re-run from touching anyone who is
-- still holding a full or larger grant (legacy accounts carry up to 150).
--
-- Subscribers are excluded on purpose. A trialling account IS is_subscribed,
-- and its whole point is a capped 10% slice of the plan (credits.py
-- TRIAL_CREDIT_FRACTION); handing it 50 more would quietly break the cap that
-- bounds what an unconverted trial can cost us.

UPDATE users
   SET credits_bonus   = 50,
       credits_balance = COALESCE(credits_daily, 0)
                       + 50
                       + COALESCE(credits_monthly, 0)
 WHERE COALESCE(is_subscribed, 0) = 0
   AND COALESCE(credits_bonus, 0) < 50;
