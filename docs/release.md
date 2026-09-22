# Release and recovery procedure

Release tags are immutable candidates: the tag-triggered workflow checks out
the exact tag, runs the backend test suite with Python 3.12, installs frontend
dependencies with Node 20 and `npm ci`, builds the frontend, and smoke-tests
the generated archive before creating a GitHub release. A failed check stops
before publication.

Create a release only after the merged pull request has passed CI:

1. Update `VERSION` and changelog entries, commit the release change, and push it.
2. Create and push the matching `vX.Y.Z` tag.
3. Confirm the Release workflow completed successfully and download the
   published checksum with the archive.
4. Update the designated test device and verify its updater health gate before
   promoting the release to production hardware.

The release archive is reproducible with respect to the committed Python source
and frontend lockfile. It is not a hermetic operating-system image: `install.sh`
and the target-side updater provision OS packages and selected platform binaries
for the device's architecture. If an update fails, the updater retains a
rollback snapshot and restores the previously running version; investigate the
update status and service logs before retrying.

## Credential lifecycle

Browser sessions last eight hours and normally survive a service restart. A
logout persists a hash of that session token until it expires, and changing the
administrator password invalidates every browser session immediately; the
browser cookie is cleared in both cases. API keys and the long-lived local CLI
service token are independent credentials and are not revoked by a password
change. Revocation records expire naturally and are capped; reaching the cap
invalidates the active browser-session generation rather than dropping older
revocations. Rotate or revoke API keys and the service token through their
respective management paths when required.
