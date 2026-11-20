# Security

Music Steward runs locally: the web UI listens on 127.0.0.1 only, behind a one-time token, and it never writes to
the collection it reads. API keys (AcoustID, Discogs) come from the environment and are never stored by the app.

To report a security problem, please open a [private security advisory](../../security/advisories/new) on GitHub
rather than a public issue.
