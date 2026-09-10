-- @id: dba.sessions
-- @title: Current user sessions
-- @description: Connected user sessions with their current wait and blocker, if any.
-- @capabilities: v_session
-- @risk: read
-- @min_version: 12
-- @parameters: row_limit
-- @privileges: SELECT on V_$SESSION
SELECT s.sid,
       s."SERIAL#"               AS serial_number,
       s.username,
       s.status,
       s.osuser,
       s.machine,
       s.program,
       s.sql_id,
       s.event,
       s.seconds_in_wait,
       s.blocking_session,
       s.blocking_session_status,
       s.last_call_et,
       s.logon_time
  FROM v$session s
 WHERE s.username IS NOT NULL
 ORDER BY s.status, s.last_call_et DESC
 FETCH FIRST :row_limit ROWS ONLY
