## What and why

<!-- What changes for the user, and why. -->

## Safety checklist

- [ ] Sources stay read-only (no write, rename or delete under a source root)
- [ ] Nothing is overwritten at the destination; interrupted runs stay resumable / undoable
- [ ] Tests added or updated (`python -m pytest`)
- [ ] `CHANGELOG.md` updated under **Unreleased** for user-visible changes
- [ ] No private data (keys, tokens, collection listings, real music files) in the diff
