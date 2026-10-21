-- Removes what 05_k7_codegen_checks.sql created. Run it as the same privileged account,
-- and only after 05 ran: it drops K7Q_USER, K7Q_OWNER and K7Q_READ by name, so it must
-- never be pointed at a database where those names belong to someone else.
-- This is the manual teardown. scripts/k7_oracle_checks.sh does not run it: it drops
-- only the names 05 reported creating (its K7Q-CREATED lines), and only after 05's
-- K7Q-PREFLIGHT-CLEAR line showed that none of them existed beforehand.
SET FEEDBACK OFF
WHENEVER SQLERROR CONTINUE
DROP USER k7q_user CASCADE;
DROP USER k7q_owner CASCADE;
DROP ROLE k7q_read;
EXIT
