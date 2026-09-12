"""The execution service: the single path from a request to a database round trip.

Everything the console, the API and the IDE adapters can make happen against Oracle
goes through this class, so policy, execution records, limits and audit are applied
once rather than per screen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from string import Template
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harness_api.config import Settings
from harness_api.models import (
    AuditEvent,
    ConnectionProfile,
    Execution,
    TargetCapability,
    UserTargetGrant,
    WorksheetSessionRecord,
    new_id,
    utcnow,
)
from harness_api.policy import (
    PERMISSION_COMPILE,
    PERMISSION_READ,
    PERMISSION_RUNBOOK,
    PERMISSION_WORKSHEET,
    PolicyDecision,
    PolicyEngine,
)
from harness_api.recovery import heartbeat, new_runtime_id, record_clean_stop
from harness_api.secrets import SecretResolver
from harness_api.security import Principal
from harness_worker.backend import ConnectionSpec, create_backend
from harness_worker.catalog import CatalogEntry, QueryCatalog, load_catalog
from harness_worker.engine import ExecutionEngine
from harness_worker.errors import (
    ConfigurationError,
    HarnessError,
    NotFoundError,
    OutcomeUnknownError,
    PolicyError,
    RuntimeSupersededError,
    ValidationError,
)
from harness_worker.sessions import SessionRegistry, WorksheetSession
from harness_worker.statement import (
    IMPLICITLY_COMMITTING_KINDS,
    bind_names,
    classify,
    fingerprint,
    is_plsql,
    prepare,
    quote_identifier,
)
from harness_worker.types import (
    TERMINAL_STATES,
    BindParameter,
    Capability,
    ExecutionLimits,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionState,
    RiskClass,
    StatementKind,
    TargetIdentity,
)

log = logging.getLogger("harness.execution")

# Object kinds a recompile runbook may name. Identifiers are quoted, but the kind is
# part of the statement itself and so has to come from a fixed list.
COMPILABLE_KINDS = (
    "PACKAGE",
    "PACKAGE BODY",
    "PROCEDURE",
    "FUNCTION",
    "TRIGGER",
    "TYPE",
    "TYPE BODY",
    "VIEW",
)


@dataclass
class OperationResult:
    """A catalog operation outcome plus the policy decision that permitted it."""

    outcome: ExecutionOutcome
    decision: PolicyDecision
    entry: CatalogEntry

    def as_dict(self) -> dict:
        return {
            "operation": self.entry.describe(),
            "policy": self.decision.as_dict(),
            "outcome": self.outcome.model_dump(by_alias=True, mode="json"),
        }


class ExecutionService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        catalog: QueryCatalog | None = None,
        runtime_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._sessions_factory = session_factory
        # Stamped on every execution and worksheet record this process owns, so a later
        # process can tell its own interrupted work from work it must not touch.
        #
        # A ``runtime_id`` passed in is one the caller claimed in the store, which makes
        # this service the store's execution owner: it heartbeats, and it stops
        # dispatching if that claim is ever taken from it. Without one -- seeding, a
        # one-off script, a fixture -- the service still stamps its records, so a later
        # startup can reconcile them, but it holds no claim and fences nothing. Only
        # ``build_state`` claims, which is what keeps one deployment to one owner.
        self._owns_store = runtime_id is not None
        self._runtime_id = runtime_id or new_runtime_id()
        self._superseded = False
        self._catalog = catalog or load_catalog(_catalog_root(settings))
        self._backend = create_backend(
            settings.oracle_backend,
            driver_mode=settings.oracle_driver_mode,
            lib_dir=settings.oracle_client_lib_dir or None,
            fake_data_dir=settings.oracle_fake_data_dir or None,
        )
        self._registry = SessionRegistry(
            self._backend, idle_timeout_seconds=settings.worksheet_idle_seconds
        )
        self._engine = ExecutionEngine(
            self._backend, self._registry, max_workers=settings.worker_processes
        )
        self._policy = PolicyEngine()
        self._secrets = SecretResolver(settings.secret_dir)
        self._reaper_stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, name="harness-reaper", daemon=True)
        self._reaper.start()

    # -- lifecycle -----------------------------------------------------------------

    def shutdown(self) -> None:
        self._reaper_stop.set()
        closed = self._registry.shutdown()
        self._engine.shutdown()
        self._backend.shutdown()
        # Close the records too, not only the connections. A stopping process is the last
        # thing that knows these sessions ended cleanly; leaving the records open would
        # make the next startup reconcile them as interrupted, and a restart that always
        # reports work makes the restart that really found some impossible to notice.
        self._close_session_records(closed, "service shutdown")
        if self._owns_store:
            record_clean_stop(self._sessions_factory, self._runtime_id)

    def _close_session_records(self, session_ids: list[str], reason: str) -> None:
        if not session_ids:
            return
        with self._sessions_factory() as db:
            for session_id in session_ids:
                record = db.get(WorksheetSessionRecord, session_id)
                if record is not None and record.closed_at is None:
                    record.closed_at = utcnow()
                    record.close_reason = reason
            db.commit()

    @property
    def runtime_id(self) -> str:
        return self._runtime_id

    @property
    def superseded(self) -> bool:
        """True once another execution service has claimed the metadata store."""

        return self._superseded

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(15.0):
            try:
                self._record_heartbeat()
                closed = self._registry.reap_expired()
            except Exception:  # noqa: BLE001 - the reaper must not die
                log.exception("Session reaper failed")
                continue
            for session_id in closed:
                with self._sessions_factory() as db:
                    record = db.get(WorksheetSessionRecord, session_id)
                    if record and record.closed_at is None:
                        record.closed_at = utcnow()
                        record.close_reason = "idle timeout"
                        db.commit()

    def _record_heartbeat(self) -> None:
        """Renew this runtime's claim on the store, and notice if it has been lost.

        Losing the claim means another process has already reconciled this one's
        in-flight work, recording writes as uncertain and statements it had not yet
        dispatched as never dispatched. Continuing to dispatch after that would make
        those records wrong, so the flag latches and every later dispatch is refused.
        """

        if self._superseded or not self._owns_store:
            return
        if heartbeat(self._sessions_factory, self._runtime_id):
            return
        self._superseded = True
        log.error(
            "recovery: runtime %s no longer owns the metadata store; another execution "
            "service has claimed it and reconciled this process's interrupted work. "
            "Refusing all further dispatch. Stop this process.",
            self._runtime_id,
        )
        reason = "this execution service no longer owns the metadata store"
        # Most of these records were already closed by the runtime that superseded this
        # one; this catches any session opened in the gap between that reconciliation and
        # this heartbeat, and leaves the earlier reason in place where there is one.
        self._close_session_records(self._registry.shutdown(reason=reason), reason)

    @property
    def catalog(self) -> QueryCatalog:
        return self._catalog

    @property
    def policy(self) -> PolicyEngine:
        return self._policy

    @property
    def backend_name(self) -> str:
        return self._backend.name

    # -- targets -------------------------------------------------------------------

    def connection_spec(
        self, profile: ConnectionProfile, grant: UserTargetGrant | None
    ) -> ConnectionSpec:
        """Resolve the credential for this actor and target.

        A grant may name its own secret reference, which is how one target can be
        reached with different Oracle accounts for different application roles.
        """

        reference = None
        if grant is not None and grant.secret_reference_id:
            reference = grant.secret_reference_id
        secret_id = reference or profile.secret_reference_id
        with self._sessions_factory() as db:
            from harness_api.models import SecretReference

            secret = db.get(SecretReference, secret_id)
            if secret is None:
                raise ConfigurationError(
                    "The credential reference for this target is missing.",
                    detail={"profileId": profile.id},
                )
            password = self._secrets.resolve(secret)
        self._check_endpoint_allowed(profile)
        return ConnectionSpec(
            profile_id=profile.id,
            host=profile.host,
            port=profile.port,
            service_name=profile.service_name,
            username=profile.username,
            password=password,
            driver_mode=self._settings.oracle_driver_mode,
            wallet_dir=profile.wallet_dir or None,
            protocol=profile.protocol,
            default_schema=profile.default_schema or None,
        )

    def _check_endpoint_allowed(self, profile: ConnectionProfile) -> None:
        allowlist = self._settings.endpoint_allowlist
        if not allowlist:
            return
        endpoint = f"{profile.host}:{profile.port}"
        if endpoint not in allowlist and profile.host not in allowlist:
            raise PolicyError(
                f"The endpoint {endpoint} is not in HARNESS_ALLOWED_ENDPOINTS.",
                detail={"endpoint": endpoint},
            )

    def probe_target(
        self, db: Session, profile: ConnectionProfile, grant: UserTargetGrant | None
    ) -> tuple[TargetIdentity, list[TargetCapability]]:
        """Connect, ask the database who it is, and probe each capability.

        Identity is never taken from the profile: two profiles pointing at the same
        service still get their own proof.
        """

        spec = self.connection_spec(profile, grant)
        connection = self._backend.connect(spec)
        try:
            identity = connection.identity()
            reports = [connection.probe_capability(cap) for cap in Capability]
        finally:
            connection.close()

        existing = {row.capability: row for row in profile.capabilities}
        rows: list[TargetCapability] = []
        for report in reports:
            row = existing.get(report.capability.value)
            if row is None:
                row = TargetCapability(profile_id=profile.id, capability=report.capability.value)
                db.add(row)
            row.available = report.available
            row.detail = report.detail
            row.checked_at = report.checked_at
            rows.append(row)
        profile.identity_json = identity.model_dump(by_alias=True, mode="json")
        profile.identity_checked_at = utcnow()
        db.commit()
        return identity, rows

    def target_identity(self, profile: ConnectionProfile) -> TargetIdentity | None:
        if not profile.identity_json:
            return None
        return TargetIdentity.model_validate(profile.identity_json)

    # -- worksheet sessions ---------------------------------------------------------

    def open_worksheet(
        self, db: Session, principal: Principal, profile: ConnectionProfile, grant: UserTargetGrant
    ) -> WorksheetSession:
        identity = self.target_identity(profile)
        self._policy.authorize(
            principal=principal,
            profile=profile,
            grant=grant,
            permission=PERMISSION_WORKSHEET,
            risk=RiskClass.READ,
            target_capabilities=list(profile.capabilities),
            target_major_version=identity.major_version if identity else None,
            operation_id="worksheet.open",
        )
        spec = self.connection_spec(profile, grant)
        session = self._registry.open(
            actor_id=principal.user_id or principal.subject,
            target_id=profile.id,
            spec=spec,
        )
        db.add(
            WorksheetSessionRecord(
                id=session.session_id,
                user_id=principal.user_id or "",
                profile_id=profile.id,
                oracle_session_id=session.identity.session_id,
                owner_id=self._runtime_id,
            )
        )
        self._audit(
            db,
            principal=principal,
            profile_id=profile.id,
            operation_id="worksheet.open",
            outcome="succeeded",
            risk=RiskClass.READ,
            detail={"sessionId": session.session_id},
        )
        db.commit()
        return session

    def worksheet(self, principal: Principal, session_id: str) -> WorksheetSession:
        return self._registry.get(session_id, principal.user_id or principal.subject)

    def list_worksheets(self, principal: Principal) -> list[WorksheetSession]:
        return self._registry.list_for_actor(principal.user_id or principal.subject)

    def _mark_session_closed(self, db: Session, session_id: str, reason: str) -> None:
        """Record that a worksheet session is gone, without committing the unit of work."""

        record = db.get(WorksheetSessionRecord, session_id)
        if record and record.closed_at is None:
            record.closed_at = utcnow()
            record.close_reason = reason

    def _mark_commit_requested(self, db: Session, session_id: str) -> None:
        """Record that a COMMIT is about to be issued on this session, and commit that.

        The write has to be durable before the COMMIT is attempted, which is why this
        commits its own unit of work rather than joining the caller's.
        """

        record = db.get(WorksheetSessionRecord, session_id)
        if record is not None:
            record.commit_requested_at = utcnow()
            db.commit()

    def _clear_commit_requested(self, db: Session, session_id: str) -> None:
        """Drop the marker once the commit's outcome is known, either way.

        Not called when the connection broke with the COMMIT in flight: there the
        marker is the evidence, and the session record is closed alongside it.
        """

        record = db.get(WorksheetSessionRecord, session_id)
        if record is not None and record.commit_requested_at is not None:
            record.commit_requested_at = None

    def close_worksheet(self, db: Session, principal: Principal, session_id: str) -> dict:
        result = self._registry.close(session_id, principal.user_id or principal.subject)
        self._mark_session_closed(db, session_id, "closed by user")
        self._audit(
            db,
            principal=principal,
            operation_id="worksheet.close",
            outcome="succeeded",
            risk=RiskClass.READ,
            detail={"sessionId": session_id, **result},
        )
        db.commit()
        return result

    def revoke_target_access(self, db: Session, *, user_id: str, target_id: str) -> list[str]:
        """Discard a user's live sessions on a target after their grant is withdrawn.

        Revocation has to reach the connections that are already open, not only the
        next request: a leased session holds an Oracle connection, and an open
        transaction on it could still be committed. Closing rolls that work back.
        """

        closed = self._registry.close_all_for_actor(
            user_id, reason="access to this target was revoked", target_id=target_id
        )
        for session_id in closed:
            record = db.get(WorksheetSessionRecord, session_id)
            if record and record.closed_at is None:
                record.closed_at = utcnow()
                record.close_reason = "access revoked"
        return closed

    def commit_worksheet(self, db: Session, principal: Principal, session_id: str) -> dict:
        session = self.worksheet(principal, session_id)
        # Owning the session is not the same as still being allowed to write to the
        # target. A grant can be narrowed or revoked, or worksheets turned off, while
        # a transaction is open; making the pending DML durable is the last and most
        # consequential step, so authorization is re-checked here rather than trusted
        # from whenever the session happened to be opened.
        self._reauthorize_session(
            db,
            principal,
            session,
            permission=PERMISSION_WORKSHEET,
            risk=RiskClass.PERSISTENT_WRITE,
            operation_id="worksheet.commit",
        )
        # A commit has no execution record of its own, so the session record carries the
        # intent instead. Committed before the COMMIT is issued and cleared once it
        # returns either way: a process that dies in between leaves this set, and restart
        # reconciliation reads it as a commit whose durability nobody observed. Without
        # it, a killed process would leave a session that merely looks abandoned, and an
        # abandoned session is otherwise a clean rollback by the database.
        self._mark_commit_requested(db, session_id)
        try:
            result = self._registry.commit(session_id, principal.user_id or principal.subject)
        except OutcomeUnknownError as exc:
            # The transaction may already be durable. An audit trail that recorded
            # nothing would be wrong, and one that recorded a failure would invite a
            # retry that applies the work a second time, so the uncertainty itself is
            # what gets written down.
            self._audit(
                db,
                principal=principal,
                profile_id=session.target_id,
                operation_id="worksheet.commit",
                outcome="outcome_unknown",
                risk=RiskClass.PERSISTENT_WRITE,
                detail={
                    "sessionId": session_id,
                    "error": exc.as_dict(),
                    "verificationRequired": True,
                },
            )
            self._mark_session_closed(db, session_id, "connection lost during commit")
            db.commit()
            raise
        except HarnessError as exc:
            # A definite refusal: the work is still pending on a session the user can
            # still reach, so the session record is left open. The commit marker goes:
            # its outcome is known, and a restart must not report it as uncertain.
            self._clear_commit_requested(db, session_id)
            self._audit(
                db,
                principal=principal,
                profile_id=session.target_id,
                operation_id="worksheet.commit",
                outcome="failed",
                risk=RiskClass.PERSISTENT_WRITE,
                detail={"sessionId": session_id, "error": exc.as_dict()},
            )
            db.commit()
            raise
        self._clear_commit_requested(db, session_id)
        self._audit(
            db,
            principal=principal,
            profile_id=session.target_id,
            operation_id="worksheet.commit",
            outcome="succeeded",
            risk=RiskClass.PERSISTENT_WRITE,
            detail={"sessionId": session_id, **result},
        )
        db.commit()
        return result

    def rollback_worksheet(self, db: Session, principal: Principal, session_id: str) -> dict:
        session = self.worksheet(principal, session_id)
        result = self._registry.rollback(session_id, principal.user_id or principal.subject)
        self._audit(
            db,
            principal=principal,
            profile_id=session.target_id,
            operation_id="worksheet.rollback",
            outcome="succeeded",
            risk=RiskClass.SESSION_WRITE,
            detail={"sessionId": session_id, **result},
        )
        db.commit()
        return result

    def cancel_worksheet(self, db: Session, principal: Principal, session_id: str) -> dict:
        session = self.worksheet(principal, session_id)
        result = self._registry.request_cancel(session_id, principal.user_id or principal.subject)
        self._audit(
            db,
            principal=principal,
            profile_id=session.target_id,
            operation_id="worksheet.cancel",
            outcome="requested",
            risk=RiskClass.READ,
            detail={"sessionId": session_id, **result},
        )
        db.commit()
        return result

    def _reauthorize_session(
        self,
        db: Session,
        principal: Principal,
        session: WorksheetSession,
        *,
        permission: str,
        risk: RiskClass,
        operation_id: str,
    ) -> PolicyDecision:
        """Re-run target authorization for a session that is already open.

        If access has gone away since the session was opened, the session goes with
        it: the connection is closed, which rolls back the uncommitted work, so a
        revoked user is not left holding a live Oracle session on the target. The
        close is forced, because a session that happens to be busy must still lose
        its access; a busy one is retired and its connection disposed of when the
        running statement returns.
        """

        try:
            profile = load_profile(db, session.target_id)
            grant = self._policy.require_grant(
                principal, profile, load_grant(db, principal, session.target_id)
            )
            identity = self.target_identity(profile)
            return self._policy.authorize(
                principal=principal,
                profile=profile,
                grant=grant,
                permission=permission,
                risk=risk,
                target_capabilities=list(profile.capabilities),
                target_major_version=identity.major_version if identity else None,
                operation_id=operation_id,
            )
        except HarnessError as exc:
            self._registry.close(
                session.session_id,
                principal.user_id or principal.subject,
                reason="access to this target was withdrawn while the session was open",
                force=True,
            )
            record = db.get(WorksheetSessionRecord, session.session_id)
            if record and record.closed_at is None:
                record.closed_at = utcnow()
                record.close_reason = "access withdrawn"
            self._audit(
                db,
                principal=principal,
                profile_id=session.target_id,
                operation_id=operation_id,
                outcome="refused",
                risk=risk,
                policy_decision="refused",
                detail={
                    "sessionId": session.session_id,
                    "code": exc.code,
                    "message": exc.message,
                    "sessionClosed": True,
                },
            )
            db.commit()
            raise

    # -- free-form worksheet statements ---------------------------------------------

    def run_worksheet_statement(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        session: WorksheetSession,
        *,
        statement: str,
        binds: list[BindParameter],
        limits: ExecutionLimits | None = None,
        dedup_key: str | None = None,
        retain_statement: bool = False,
    ) -> tuple[ExecutionOutcome, PolicyDecision]:
        prepared, kind = prepare(statement)
        risk = _risk_for_kind(kind)
        permission = (
            PERMISSION_COMPILE if kind == StatementKind.PLSQL_SOURCE else PERMISSION_WORKSHEET
        )
        identity = self.target_identity(profile)

        if kind in IMPLICITLY_COMMITTING_KINDS and session.transaction_open:
            # Oracle would commit the pending DML as a side effect. Refuse rather than
            # surprise the user with a commit they did not ask for. A CREATE OR REPLACE
            # program unit is DDL too, which is why it is caught here and not only in
            # the DDL branch: /api/v1/plsql/compile exists precisely so compilation can
            # run on its own connection, clear of any worksheet transaction.
            #
            # This is the early refusal, made before an execution record exists, and it
            # reads the transaction state without the session lock. The engine makes
            # the same check again once it holds that lock, which is the one that
            # actually decides: another statement in this session can commit DML into
            # the gap between here and there.
            raise PolicyError(
                "This session has uncommitted changes and the statement is DDL, which "
                "would commit them. Commit or roll back first, then run the DDL.",
                detail={"sessionId": session.session_id, "statementKind": kind.value},
            )

        decision = self._policy.authorize(
            principal=principal,
            profile=profile,
            grant=grant,
            permission=permission,
            risk=risk,
            target_capabilities=list(profile.capabilities),
            target_major_version=identity.major_version if identity else None,
            operation_id="worksheet.execute",
        )

        effective = self._settings.default_limits
        if limits is not None:
            effective = effective.narrowed_to(limits)

        request_digest = (
            _worksheet_request_digest(prepared, binds, effective) if dedup_key else None
        )
        duplicate = self._existing_dispatch(
            db,
            actor_id=principal.user_id or "",
            session_id=session.session_id,
            dedup_key=dedup_key,
            request_digest=request_digest,
        )
        if duplicate is not None:
            return duplicate, decision

        execution_id = new_id("exe")
        record = self._begin_execution(
            db,
            execution_id=execution_id,
            principal=principal,
            profile=profile,
            session_id=session.session_id,
            operation_id="worksheet.execute",
            statement=prepared,
            kind=kind,
            risk=risk,
            limits=effective,
            decision=decision,
            dedup_key=dedup_key,
            retain_statement=retain_statement,
            request_digest=request_digest,
        )

        request = ExecutionRequest(
            executionId=execution_id,
            operationId="worksheet.execute",
            statement=prepared,
            binds=binds,
            statementKind=kind,
            limits=effective,
            collectDbmsOutput=is_plsql(kind),
        )
        self._mark_dispatched(db, record)
        try:
            outcome = self._engine.execute_in_session(session, request)
        except HarnessError as exc:
            self._fail_execution(db, record, exc)
            raise
        self._finish_execution(db, record, outcome, principal, profile.id, risk)
        return outcome, decision

    # -- reviewed catalog operations ------------------------------------------------

    def run_catalog_operation(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        operation_id: str,
        parameters: dict[str, Any] | None = None,
        *,
        row_limit: int | None = None,
        confirm: bool = False,
        runner: Callable[[ExecutionRequest], ExecutionOutcome] | None = None,
    ) -> OperationResult:
        """Run one reviewed catalog query.

        ``runner`` lets a caller supply the connection instead of taking a fresh one
        per statement, for the operations that only mean anything in the session that
        produced the data they read. Everything above it -- authorization, limits and
        the execution record -- is unchanged.
        """

        entry = self._catalog.get(operation_id)
        identity = self.target_identity(profile)
        permission = self._policy.permission_for_entry(entry)

        if entry.risk in (RiskClass.ADMINISTRATIVE, RiskClass.PERSISTENT_WRITE) and not confirm:
            raise ValidationError(
                f"{entry.title} changes the database. Re-send the request with an "
                "explicit confirmation to run it.",
                detail={
                    "operationId": entry.operation_id,
                    "risk": entry.risk.value,
                    "requiresConfirmation": True,
                },
            )

        decision = self._policy.authorize(
            principal=principal,
            profile=profile,
            grant=grant,
            permission=permission,
            risk=entry.risk,
            required_capabilities=entry.capabilities,
            target_capabilities=list(profile.capabilities),
            min_version=entry.min_version,
            target_major_version=identity.major_version if identity else None,
            operation_id=entry.operation_id,
        )

        statement, binds = self._bind_entry(entry, parameters or {}, row_limit)
        kind = classify(statement)
        limits = self._settings.default_limits
        if row_limit:
            limits = limits.narrowed_to(
                ExecutionLimits(maxRows=row_limit, **_other_limit_fields(limits))
            )

        execution_id = new_id("exe")
        record = self._begin_execution(
            db,
            execution_id=execution_id,
            principal=principal,
            profile=profile,
            session_id=None,
            operation_id=entry.operation_id,
            statement=statement,
            kind=kind,
            risk=entry.risk,
            limits=limits,
            decision=decision,
            dedup_key=None,
            retain_statement=True,
        )

        request = ExecutionRequest(
            executionId=execution_id,
            operationId=entry.operation_id,
            statement=statement,
            binds=binds,
            limits=limits,
            collectDbmsOutput=False,
            autocommit=entry.risk in (RiskClass.ADMINISTRATIVE, RiskClass.PERSISTENT_WRITE),
        )
        self._mark_dispatched(db, record)
        try:
            if runner is not None:
                outcome = runner(request)
            else:
                outcome = self._engine.execute_once(self.connection_spec(profile, grant), request)
        except HarnessError as exc:
            self._fail_execution(db, record, exc)
            raise
        self._finish_execution(db, record, outcome, principal, profile.id, entry.risk)
        return OperationResult(outcome=outcome, decision=decision, entry=entry)

    def _bind_entry(
        self, entry: CatalogEntry, parameters: dict[str, Any], row_limit: int | None
    ) -> tuple[str, list[BindParameter]]:
        """Fill in a catalog entry, separating identifiers from bind values."""

        statement = entry.sql
        if entry.identifier_parameters:
            substitutions: dict[str, str] = {}
            for name in entry.identifier_parameters:
                if name not in parameters or parameters[name] in (None, ""):
                    raise ValidationError(
                        f"{entry.operation_id} requires the parameter {name!r}.",
                        detail={"parameter": name},
                    )
                value = str(parameters[name])
                if name == "object_kind":
                    kind = " ".join(value.upper().split())
                    if kind not in COMPILABLE_KINDS:
                        raise ValidationError(
                            "object_kind must be one of: " + ", ".join(COMPILABLE_KINDS),
                            detail={"objectKind": value},
                        )
                    # A body is altered through its owner: ALTER PACKAGE x COMPILE BODY.
                    # ALTER PACKAGE BODY x COMPILE is ORA-00922, found against 19c.
                    alter_kind, _, body = kind.partition(" ")
                    substitutions[name] = alter_kind
                    substitutions["compile_clause"] = "COMPILE BODY" if body else "COMPILE"
                else:
                    substitutions[name] = quote_identifier(value.upper())
            try:
                statement = Template(statement).substitute(substitutions)
            except KeyError as exc:  # pragma: no cover - catalog authoring error
                raise ConfigurationError(
                    f"{entry.operation_id} references an undeclared identifier parameter {exc}.",
                ) from exc

        supplied = {name: parameters.get(name) for name in entry.parameters}
        if "row_limit" in supplied:
            requested = supplied["row_limit"] or row_limit or self._settings.max_rows
            supplied["row_limit"] = max(1, min(int(requested), self._settings.max_rows))
        if "row_offset" in supplied:
            supplied["row_offset"] = max(0, int(supplied["row_offset"] or 0))

        referenced = set(bind_names(statement))
        unknown = referenced - set(supplied)
        if unknown:
            raise ConfigurationError(
                f"{entry.operation_id} references binds that its header does not "
                f"declare: {', '.join(sorted(unknown))}.",
                detail={"operationId": entry.operation_id},
            )
        return statement, [
            BindParameter(name=name, value=value)
            for name, value in supplied.items()
            if name in referenced
        ]

    # -- PL/SQL compilation ------------------------------------------------------------

    def compile_plsql(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        source: str,
    ) -> tuple[ExecutionOutcome, PolicyDecision]:
        """Compile a program unit in its own session.

        Compilation is DDL and commits. Running it on a fresh connection keeps it out
        of the user worksheet transaction, so it can never commit pending DML the user
        has not chosen to commit.
        """

        prepared, kind = prepare(source)
        if kind != StatementKind.PLSQL_SOURCE:
            raise ValidationError(
                "The compile endpoint takes a CREATE OR REPLACE program unit. Use the "
                "worksheet to run other statements.",
                detail={"statementKind": kind.value},
            )
        identity = self.target_identity(profile)
        decision = self._policy.authorize(
            principal=principal,
            profile=profile,
            grant=grant,
            permission=PERMISSION_COMPILE,
            risk=RiskClass.PERSISTENT_WRITE,
            required_capabilities=(Capability.COMPILE_OBJECTS,),
            target_capabilities=list(profile.capabilities),
            target_major_version=identity.major_version if identity else None,
            operation_id="plsql.compile",
        )
        execution_id = new_id("exe")
        record = self._begin_execution(
            db,
            execution_id=execution_id,
            principal=principal,
            profile=profile,
            session_id=None,
            operation_id="plsql.compile",
            statement=prepared,
            kind=kind,
            risk=RiskClass.PERSISTENT_WRITE,
            limits=self._settings.default_limits,
            decision=decision,
            dedup_key=None,
            retain_statement=False,
        )
        spec = self.connection_spec(profile, grant)
        request = ExecutionRequest(
            executionId=execution_id,
            operationId="plsql.compile",
            statement=prepared,
            limits=self._settings.default_limits,
            collectDbmsOutput=False,
            autocommit=True,
        )
        self._mark_dispatched(db, record)
        try:
            outcome = self._engine.execute_once(spec, request)
        except HarnessError as exc:
            self._fail_execution(db, record, exc)
            raise
        self._finish_execution(
            db, record, outcome, principal, profile.id, RiskClass.PERSISTENT_WRITE
        )
        return outcome, decision

    # -- explain plan ----------------------------------------------------------------

    def explain_statement(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        statement: str,
    ) -> tuple[str, ExecutionOutcome, OperationResult | None]:
        """Ask the optimizer for an estimated plan without running the statement.

        The plan is written and read back on one connection. PLAN_TABLE is a global
        temporary table on a default 19c install, so its rows belong to the session
        that inserted them: a read on a second connection returns nothing at all.
        """

        prepared, kind = prepare(statement)
        if kind not in (StatementKind.QUERY, StatementKind.DML):
            raise ValidationError(
                "Only a query or a DML statement can be explained.",
                detail={"statementKind": kind.value},
            )
        identity = self.target_identity(profile)
        decision = self._policy.authorize(
            principal=principal,
            profile=profile,
            grant=grant,
            permission=PERMISSION_WORKSHEET,
            risk=RiskClass.READ,
            required_capabilities=(Capability.EXPLAIN_PLAN,),
            target_capabilities=list(profile.capabilities),
            target_major_version=identity.major_version if identity else None,
            operation_id="tuning.explain",
        )
        # STATEMENT_ID cannot be a bind, so it is a generated hex token rather than
        # anything a caller supplied.
        statement_id = f"h{uuid.uuid4().hex[:24]}"
        execution_id = new_id("exe")
        explain_sql = f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {prepared}"
        record = self._begin_execution(
            db,
            execution_id=execution_id,
            principal=principal,
            profile=profile,
            session_id=None,
            operation_id="tuning.explain",
            statement=prepared,
            kind=kind,
            risk=RiskClass.READ,
            limits=self._settings.default_limits,
            decision=decision,
            dedup_key=None,
            retain_statement=False,
        )
        spec = self.connection_spec(profile, grant)
        request = ExecutionRequest(
            executionId=execution_id,
            operationId="tuning.explain",
            statement=explain_sql,
            limits=self._settings.default_limits,
            autocommit=True,
        )
        self._mark_dispatched(db, record)
        with self._engine.one_connection(spec) as run:
            try:
                outcome = run(request)
            except HarnessError as exc:
                self._fail_execution(db, record, exc)
                raise
            self._finish_execution(db, record, outcome, principal, profile.id, RiskClass.READ)
            rows_result = None
            if outcome.state == ExecutionState.SUCCEEDED:
                rows_result = self.run_catalog_operation(
                    db,
                    principal,
                    profile,
                    grant,
                    "tuning.explain_plan_rows",
                    {"statement_id": statement_id},
                    runner=run,
                )
            # An explain that failed wrote no rows, so there is nothing to read back.
            # The caller reports the failure rather than an empty plan with no reason.
        return statement_id, outcome, rows_result

    # -- execution records and audit -------------------------------------------------

    def _existing_dispatch(
        self,
        db: Session,
        *,
        actor_id: str,
        session_id: str | None,
        dedup_key: str | None,
        request_digest: str | None,
    ) -> ExecutionOutcome | None:
        """Return the earlier execution for this idempotency key, if there is one.

        Deduplication is a property of the durable execution record, so it survives a
        restart. It prevents a second *dispatch*; it does not make execution
        exactly-once inside Oracle, and the returned outcome says so.

        A key is scoped to the actor and the worksheet session that used it. Keys are
        chosen by clients, so two users will pick the same string sooner or later, and
        a global lookup would hand one user the other's execution record instead of
        running their statement. Reuse within one actor for a different statement is
        refused rather than deduplicated, for the same reason: the caller would be
        told their statement had already run when a different one had.
        """

        if not dedup_key:
            return None
        existing = db.scalars(
            select(Execution).where(
                Execution.user_id == actor_id,
                Execution.dedup_key == dedup_key,
            )
        ).first()
        if existing is None:
            return None
        if (
            existing.session_id != session_id
            or request_digest is None
            or existing.request_digest != request_digest
        ):
            raise ValidationError(
                "This idempotency key was already used for a different request. A key "
                "identifies one request; reusing it here would report the outcome of "
                "the earlier statement instead of running this one. Choose a new key.",
                detail={
                    "idempotencyKey": dedup_key,
                    "originalExecutionId": existing.id,
                },
            )
        return ExecutionOutcome(
            executionId=existing.id,
            state=ExecutionState(existing.state),
            statementKind=StatementKind(existing.statement_kind),
            rowsAffected=existing.rows_affected,
            elapsedMs=existing.elapsed_ms or 0,
            warnings=[
                "This request was already dispatched under the same idempotency key. "
                "Deduplication prevents a second dispatch; it does not guarantee the "
                "original statement ran exactly once inside Oracle."
            ],
            verification={
                **(existing.verification_json or {}),
                "deduplicated": True,
                "originalExecutionId": existing.id,
            },
        )

    def _begin_execution(
        self,
        db: Session,
        *,
        execution_id: str,
        principal: Principal,
        profile: ConnectionProfile,
        session_id: str | None,
        operation_id: str,
        statement: str,
        kind: StatementKind,
        risk: RiskClass,
        limits: ExecutionLimits,
        decision: PolicyDecision,
        dedup_key: str | None,
        retain_statement: bool,
        request_digest: str | None = None,
    ) -> Execution:
        """Persist intent before dispatch so interrupted work can be reconciled."""

        record = Execution(
            id=execution_id,
            user_id=principal.user_id or "",
            profile_id=profile.id,
            session_id=session_id,
            operation_id=operation_id,
            statement_kind=kind.value,
            risk_class=risk.value,
            statement_fingerprint=fingerprint(statement),
            statement_text=statement if retain_statement else None,
            bind_names=bind_names(statement),
            limits_json=limits.model_dump(by_alias=True, mode="json"),
            policy_decision="allowed" if decision.allowed else "refused",
            policy_reason=decision.reason,
            state=ExecutionState.QUEUED.value,
            dedup_key=dedup_key,
            request_digest=request_digest,
            owner_id=self._runtime_id,
        )
        db.add(record)
        db.commit()
        return record

    def _mark_dispatched(self, db: Session, record: Execution) -> None:
        """Commit the moment this statement stops being merely intended.

        Called immediately before the engine is handed the request, and committed, so
        that a store surviving this process distinguishes two situations a single
        ``queued`` row cannot: work that never reached a connection, and work that was
        in flight. Restart reconciliation resolves the first to ``cancelled`` and the
        second to ``outcome_unknown``; without this marker it would have to treat every
        interrupted write as uncertain.

        The cost is one extra round trip to the metadata store per execution. That buys
        the difference between telling an operator "this did not run" and telling them
        "this may have run", which is the whole point of persisting intent.

        This is also where a runtime that has lost ownership of the store stops. By then
        another process has already recorded this one's work, so dispatching would run a
        statement whose record says it never was.
        """

        if self._superseded:
            raise RuntimeSupersededError(
                "This execution service no longer owns the metadata store: another one "
                "has claimed it and has already reconciled this process's work. Nothing "
                "was dispatched. Use the service that owns the store.",
                detail={"runtimeId": self._runtime_id, "executionId": record.id},
            )
        record.state = ExecutionState.RUNNING.value
        record.dispatched_at = utcnow()
        db.add(record)
        db.commit()

    def _already_settled(self, db: Session, record: Execution) -> bool:
        """True if something else has already given this record a terminal state.

        The only thing that does is restart reconciliation, run by a process that took
        ownership of the store while this statement was still inside the driver call. Its
        verdict has to stand. A late success written over it would erase the record an
        operator is working from -- and they are the one who can still go and look in the
        database, which this process, having lost the store, cannot be trusted to do.

        Refused rather than merged, and logged, because it means two execution services
        overlapped on one store, which is a deployment fault worth seeing.
        """

        db.refresh(record)
        try:
            settled = ExecutionState(record.state) in TERMINAL_STATES
        except ValueError:  # a state this build does not know: leave it alone
            settled = True
        if not settled:
            return False
        log.error(
            "execution %s was already resolved as %s by another process while this one "
            "was still running it. Keeping that outcome; not overwriting it.",
            record.id,
            record.state,
        )
        return True

    def _finish_execution(
        self,
        db: Session,
        record: Execution,
        outcome: ExecutionOutcome,
        principal: Principal,
        profile_id: str,
        risk: RiskClass,
    ) -> None:
        if self._already_settled(db, record):
            return
        record.state = outcome.state.value
        record.elapsed_ms = outcome.elapsed_ms
        record.database_elapsed_ms = outcome.database_elapsed_ms
        record.rows_returned = outcome.result_set.row_count if outcome.result_set else None
        record.rows_affected = outcome.rows_affected
        record.truncated = bool(outcome.result_set and outcome.result_set.truncated)
        record.verification_json = outcome.verification
        record.finished_at = outcome.finished_at or utcnow()
        if outcome.error:
            record.error_code = str(outcome.error.get("code", ""))[:60]
            record.error_message = str(outcome.error.get("message", ""))
        db.add(record)
        self._audit(
            db,
            principal=principal,
            profile_id=profile_id,
            operation_id=record.operation_id,
            execution_id=record.id,
            outcome=outcome.state.value,
            risk=risk,
            fingerprint=record.statement_fingerprint,
            affected={
                "rowsReturned": record.rows_returned,
                "rowsAffected": record.rows_affected,
                "truncated": record.truncated,
            },
        )
        db.commit()

    def _fail_execution(self, db: Session, record: Execution, error: HarnessError) -> None:
        """Record a dispatch that raised instead of producing an outcome of its own.

        An error whose fate inside Oracle is unknown -- a connection that broke with
        a one-shot autocommit in flight, above all -- is never written down as a
        failure. A caller reading ``failed`` will retry, and retrying a write that
        may already have been applied applies it twice, so the uncertainty is kept
        as the state and flagged for verification.
        """

        if self._already_settled(db, record):
            return
        unknown = isinstance(error, OutcomeUnknownError)
        record.state = (
            ExecutionState.OUTCOME_UNKNOWN.value if unknown else ExecutionState.FAILED.value
        )
        record.error_code = error.code
        record.error_message = error.message
        if unknown:
            record.verification_json = {
                **(record.verification_json or {}),
                **error.detail,
                "state": ExecutionState.OUTCOME_UNKNOWN.value,
                "verificationRequired": True,
            }
        record.finished_at = utcnow()
        db.add(record)
        db.commit()

    def _audit(
        self,
        db: Session,
        *,
        principal: Principal,
        operation_id: str,
        outcome: str,
        risk: RiskClass,
        profile_id: str | None = None,
        execution_id: str | None = None,
        fingerprint: str = "",
        affected: dict | None = None,
        detail: dict | None = None,
        policy_decision: str = "allowed",
    ) -> None:
        db.add(
            AuditEvent(
                actor_id=principal.user_id or "",
                actor_subject=principal.subject,
                integration_id=principal.integration_id,
                profile_id=profile_id,
                operation_id=operation_id,
                execution_id=execution_id,
                risk_class=risk.value,
                policy_decision=policy_decision,
                outcome=outcome,
                statement_fingerprint=fingerprint,
                affected_counts=affected or {},
                detail=detail or {},
            )
        )

    def audit_refusal(
        self,
        db: Session,
        principal: Principal,
        *,
        operation_id: str,
        profile_id: str | None,
        error: HarnessError,
    ) -> None:
        self._audit(
            db,
            principal=principal,
            profile_id=profile_id,
            operation_id=operation_id,
            outcome="refused",
            risk=RiskClass.READ,
            policy_decision="refused",
            detail={"code": error.code, "message": error.message},
        )
        db.commit()


def _worksheet_request_digest(
    statement: str, binds: list[BindParameter], limits: ExecutionLimits
) -> str:
    """Hash exact execution inputs independently of the redacted audit fingerprint.

    Named bind ordering is immaterial. Values and their JSON types, type hints,
    SQL literals and effective execution limits all participate in request identity.
    Only the digest is persisted, before dispatch; legacy records without one must
    not be treated as verified retries.
    """

    payload = {
        "version": 1,
        "statement": statement,
        "binds": [
            bind.model_dump(mode="json") for bind in sorted(binds, key=lambda bind: bind.name)
        ],
        "limits": limits.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _risk_for_kind(kind: StatementKind) -> RiskClass:
    if kind in (StatementKind.QUERY,):
        return RiskClass.READ
    if kind == StatementKind.DML:
        return RiskClass.SESSION_WRITE
    if kind in (StatementKind.DDL, StatementKind.PLSQL_SOURCE):
        return RiskClass.PERSISTENT_WRITE
    if kind == StatementKind.PLSQL_BLOCK:
        # A block can commit on its own, so it is treated as persistent even though
        # many blocks are read-only in practice.
        return RiskClass.PERSISTENT_WRITE
    if kind == StatementKind.TRANSACTION_CONTROL:
        return RiskClass.PERSISTENT_WRITE
    return RiskClass.SESSION_WRITE


def _other_limit_fields(limits: ExecutionLimits) -> dict[str, Any]:
    return {
        "maxResponseBytes": limits.max_response_bytes,
        "deadlineSeconds": limits.deadline_seconds,
        "maxDbmsOutputBytes": limits.max_dbms_output_bytes,
        "lobPreviewBytes": limits.lob_preview_bytes,
    }


def _catalog_root(settings: Settings):
    from harness_worker.catalog import default_catalog_root

    return default_catalog_root()


def load_profile(db: Session, profile_id: str) -> ConnectionProfile:
    profile = db.get(ConnectionProfile, profile_id)
    if profile is None:
        raise NotFoundError("No such connection profile.", detail={"profileId": profile_id})
    return profile


def load_grant(db: Session, principal: Principal, profile_id: str) -> UserTargetGrant | None:
    if principal.user_id is None:
        return None
    return db.scalars(
        select(UserTargetGrant).where(
            UserTargetGrant.user_id == principal.user_id,
            UserTargetGrant.profile_id == profile_id,
        )
    ).first()


__all__ = [
    "ExecutionService",
    "OperationResult",
    "load_grant",
    "load_profile",
    "PERMISSION_READ",
    "PERMISSION_RUNBOOK",
    "PERMISSION_WORKSHEET",
    "PERMISSION_COMPILE",
]
