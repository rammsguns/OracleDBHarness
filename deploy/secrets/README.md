# Secret files

Every file in this directory is ignored by git except this one and `.gitignore`.

The application never stores a password. A `SecretReference` record names a provider
(`file` or `env`) and a locator; the value is read at connection time and discarded.
Nothing here is written back to the metadata store, returned by the API, or logged.

Create the files Compose expects, with no trailing newline beyond the one your shell
adds (a single trailing newline is stripped):

```bash
printf '%s' 'the postgres password'      > deploy/secrets/postgres_password
printf '%s' 'the oracle account password'> deploy/secrets/oracle_app.password
printf '%s' 'the model provider api key' > deploy/secrets/provider_api_key
chmod 700 deploy/secrets       # nobody else on this host can look inside
chmod 644 deploy/secrets/*     # the API container's unprivileged user can read them
```

Those two modes are deliberate, and `chmod 600` on the files does not work. Compose
bind-mounts each file into the container with its ownership and mode from the host, and the
API runs as an unprivileged user (uid 10001, `deploy/Dockerfile.api`) which is not the
operator who created the files. A mode of `600` means that user cannot read the secret, and
the API exits at startup with `PermissionError: /run/secrets/postgres_password`.

Confusingly, PostgreSQL in the same deployment is unaffected: its entrypoint reads
`POSTGRES_PASSWORD_FILE` as root before dropping to the `postgres` user, so a `600` file
works there and the failure looks like it is only about the API.

The secrets are still protected on the host. Permissions on the *directory* are what keep
other users out, and a bind-mounted file is opened through the mount rather than by walking
the host path, so `700` on the directory costs the container nothing.

Then register the reference in the harness so a profile can point at it:

```
POST /api/v1/admin/secrets
{ "name": "oracle-app", "provider": "file", "locator": "oracle_app.password" }
```

A locator cannot escape the configured secret directory; `../` is refused.

For a real deployment, replace the file provider with your secret manager by adding
a provider to `services/api/harness_api/secrets.py`. The rest of the application
does not change: it only ever asks for a reference to be resolved.
