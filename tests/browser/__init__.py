"""Console sign-in in a real browser: the NP-06 qualification entry point.

``tests/identity`` checks the provider-facing code against Keycloak, under Python and
Node, without a browser. This package drives Chromium through the console's own sign-in:
the redirect to the provider, the callback, API calls with the token the console holds,
expiry, sign-out, a refused identity and direct API access without the right credentials.

It runs in one of four modes, and the report says which. Only one of them qualifies
anything about the pilot:

``rehearsal``
    Everything local, including a small stand-in identity provider started by the run
    (``stub_provider.py``). For developing and checking this tooling on a machine with no
    identity provider. Evidence about the tooling, not about any provider.
``fixture``
    The disposable Keycloak realm in ``tests/identity/keycloak``, with the API and a
    ``vite preview`` of the console started locally. Evidence about the console against a
    real provider registered as docs/setup.md says; not about the pilot's registration,
    its deployed origin or its proxy.
``standin``
    A deployed console origin and an external identity provider (such as the Keycloak
    fixture realm). Like ``pilot``, nothing is started by the run; unlike ``pilot``,
    automated form login and test provider issuers are permitted for agent qualification.
    Its report verdict is ``AGENT-QUALIFIED ON TEST STAND-IN``; it never qualifies the pilot.
``pilot``
    A deployed console origin and the pilot's own identity registration. Nothing is
    started; the person running it signs in by hand in the browser window, so no
    provider-specific login automation and no pilot credential is involved. The only
    mode whose report can say ``QUALIFIED``.

Run with ``uv run --group browser python -m tests.browser run``. See
``tests/identity/README.md``, "Browser qualification".
"""
