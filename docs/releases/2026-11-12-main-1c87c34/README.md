# Historical default-branch release artifacts

This archive preserves exact Git blob bytes from commit `1c87c34c45d74939d72293fddb7f7dd2c3c3128e`: the previous standalone lock, frontend build, manifest, checksums, and security report. These describe that historical snapshot only and do not certify current source or a new release. Existing tags and GitHub releases remain unchanged. `files.json` records the SHA-256 of each archived blob.

The active source repository uses the root workspace lock. Generate fresh standalone artifacts with `tools/release_package.py`; do not copy the archived lock or build into the active source tree.
