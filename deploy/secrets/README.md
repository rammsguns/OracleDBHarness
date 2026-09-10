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
chmod 600 deploy/secrets/*
```

Then register the reference in the harness so a profile can point at it:

```
POST /api/v1/admin/secrets
{ "name": "oracle-app", "provider": "file", "locator": "oracle_app.password" }
```

A locator cannot escape the configured secret directory; `../` is refused.

For a real deployment, replace the file provider with your secret manager by adding
a provider to `services/api/harness_api/secrets.py`. The rest of the application
does not change: it only ever asks for a reference to be resolved.
