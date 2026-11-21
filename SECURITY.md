# Security

Music Steward runs locally: the web UI listens on 127.0.0.1 only, behind a one-time token, and it does not write to
the collection it reads — with one optional, off-by-default exception: *Retire originals* can move files of verified
albums into an `_ORIGINALS` folder in the same collection folder when the user allows it and confirms (never deleting). API keys (AcoustID, Discogs) come from the environment and are never stored by the app.

To report a security problem, please open a [private security advisory](../../security/advisories/new) on GitHub
rather than a public issue.
