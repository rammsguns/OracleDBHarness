"""Copilot endpoints, shared by the web console and every IDE adapter.

Two kinds of caller are accepted:

* A signed-in harness user, on their own authority.
* A registered IDE adapter presenting a scoped integration credential and asserting
  which of *its* users is acting. The harness namespaces that actor by integration
  instance. A browser-supplied role is never accepted as authority.

The response is a typed event stream: start, delta, proposal, usage, done, error.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from harness_api.copilot import CopilotAsk, EditorReference
from harness_api.deps import CurrentUser, Db, State, get_db, get_state
from harness_api.schemas import ApplyCheckIn, ContextPreviewIn, CopilotRequestIn
from harness_api.security import Principal
from harness_worker.errors import AuthenticationError, HarnessError

router = APIRouter(prefix="/api/v1", tags=["copilot"])


def _resolve_principal(
    state: State,
    db,
    authorization: str | None,
    *,
    actor_reference: str | None = None,
    actor_is_durable: bool = True,
) -> Principal:
    """Accept either a user token or an integration credential."""

    if not authorization:
        raise AuthenticationError("An Authorization header is required.")
    token = authorization.partition(" ")[2].strip()
    if token.startswith("odbh_"):
        principal = state.authenticator.authenticate_integration(
            db,
            authorization,
            actor_reference=actor_reference,
            actor_is_durable=actor_is_durable,
        )
        db.commit()
        return principal
    return state.authenticator.authenticate(db, authorization)


@router.get("/integrations/capabilities")
def integration_capabilities(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """What this harness supports. Carries no secrets and takes no source text."""

    state = get_state(request)
    db = next(get_db(request))
    try:
        principal = _resolve_principal(state, db, authorization)
        capabilities = state.copilot.capabilities()
        capabilities["actor"] = {
            "integrationId": principal.integration_id,
            "durableIdentity": principal.durable_identity,
        }
        capabilities["grantedScopes"] = list(principal.integration_scopes)
        return capabilities
    finally:
        db.close()


@router.post("/copilot/context/preview")
def context_preview(
    payload: ContextPreviewIn, principal: CurrentUser, state: State
) -> dict[str, Any]:
    """Show the user exactly what would be sent, before anything is sent."""

    context = state.copilot.context_policy.build(
        target_reference=payload.target_reference,
        raw_attachments=[a.model_dump() for a in payload.attachments],
        database_version=payload.database_version,
        schema=payload.schema_name,
    )
    preview = context.preview()
    preview["provider"] = state.settings.copilot_provider
    preview["model"] = state.settings.copilot_model
    preview["enabled"] = state.copilot.enabled
    return preview


@router.post("/copilot/requests")
async def copilot_request(
    payload: CopilotRequestIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    state = get_state(request)
    db = next(get_db(request))
    try:
        principal = _resolve_principal(
            state,
            db,
            authorization,
            actor_reference=payload.actor_reference,
            actor_is_durable=payload.actor_is_durable,
        )
    finally:
        db.close()

    async def events() -> AsyncIterator[bytes]:
        try:
            context = state.copilot.context_policy.build(
                target_reference=payload.target_reference,
                raw_attachments=[a.model_dump() for a in payload.attachments],
                database_version=payload.database_version,
                schema=payload.schema_name,
            )
        except HarnessError as exc:
            yield _sse("error", exc.as_dict())
            return

        ask = CopilotAsk(
            action=payload.action,
            user_message=payload.user_message,
            target_reference=payload.target_reference,
            context=context,
            editor=EditorReference(
                editor_id=payload.editor.editor_id,
                revision=payload.editor.revision,
                text=payload.editor.text,
            ),
            conversation_id=payload.conversation_id,
            protocol_version=payload.protocol_version,
        )
        stream = state.copilot.run(principal, ask)
        try:
            async for name, data in stream:
                if await request.is_disconnected():
                    # The caller went away. Stop consuming the provider rather than
                    # finishing a request nobody is waiting for; a partly delivered
                    # request is never replayed automatically.
                    break
                yield _sse(name, data)
        finally:
            # Close the stream here rather than leaving it to the garbage collector,
            # so an abandoned request is recorded as cancelled while we still know
            # how long it ran for.
            await stream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/copilot/proposals/{proposal_id}/apply-check")
def apply_check(
    proposal_id: str,
    payload: ApplyCheckIn,
    request: Request,
    db: Db,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Decide whether a proposal may still be applied to this buffer.

    Applying updates the editor only. It never runs, compiles, commits or pushes
    anything.
    """

    state = get_state(request)
    principal = _resolve_principal(
        state, db, authorization, actor_reference=payload.actor_reference
    )
    return state.copilot.check_apply(
        db,
        principal,
        proposal_id,
        editor_id=payload.editor_id,
        revision=payload.revision,
        current_text=payload.current_text,
        target_reference=payload.target_reference,
    )


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()
