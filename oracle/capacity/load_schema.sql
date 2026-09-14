-- Reviewed marker table for the capacity run (tests/capacity, docs/capacity.md).
--
-- Apply to the load schema of each of the three load databases, as that schema's owner,
-- on NON-PRODUCTION databases only. The capacity run writes one row per transaction it
-- commits or rolls back, names the run, the target it meant to write to, the user and a
-- per-user sequence number, and compares what each database holds with what it did.
--
-- The primary key is deliberate: a write the harness replayed would fail with ORA-00001
-- and be counted, rather than appear as an extra row nobody notices.
--
-- The run deletes its own rows at the end when the workload says "cleanup": true.
-- Remove the table with load_teardown.sql once capacity testing is over.

CREATE TABLE harness_load_markers (
  run_id      VARCHAR2(40)  NOT NULL,
  target_name VARCHAR2(100) NOT NULL,
  user_name   VARCHAR2(255) NOT NULL,
  seq         NUMBER(12)    NOT NULL,
  created_at  TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT harness_load_markers_pk PRIMARY KEY (run_id, user_name, seq)
);

-- The load accounts write here through their worksheet sessions. If the table lives in a
-- schema other than the accounts' own, grant them exactly this, and give their profiles
-- that schema as the default schema:
--
-- GRANT SELECT, INSERT, DELETE ON harness_load_markers TO <load account>;
