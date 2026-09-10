-- @id: dba.blocking
-- @title: Blocked sessions and their blockers
-- @description: Sessions waiting on another session, paired with the holder.
-- @capabilities: v_session
-- @risk: read
-- @min_version: 12
-- @privileges: SELECT on V_$SESSION
SELECT w.sid                AS waiting_sid,
       w.username           AS waiting_user,
       w.event              AS waiting_event,
       w.seconds_in_wait    AS waiting_seconds,
       w.sql_id             AS waiting_sql_id,
       b.sid                AS blocking_sid,
       b.username           AS blocking_user,
       b.status             AS blocking_status,
       b.program            AS blocking_program,
       b.last_call_et       AS blocking_idle_seconds
  FROM v$session w
  JOIN v$session b
    ON b.sid = w.blocking_session
 WHERE w.blocking_session IS NOT NULL
 ORDER BY w.seconds_in_wait DESC
