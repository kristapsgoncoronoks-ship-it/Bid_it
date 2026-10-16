# V05 follow-up: exclude runtime object storage from backend images

AI-assisted engineering and verification; pending human review and exact-head CI.

The backend Docker context previously omitted local database/env files but did not
exclude `var/`, the local runtime object-storage directory. Four local runtime files
(18,255 bytes) were present during inspection. Their contents were not inspected.
This is a packaging exposure path; it is not evidence of production data disclosure.

`backend/.dockerignore` now excludes `var/`. The Dockerfile creates an empty directory
owned by UID 10001 for development/runtime writes. CI seeds a synthetic file before
building, then runs the final image as its normal user and asserts the directory is
empty and writable. This verifies Docker behavior, not just a configuration string.

A fresh local backend image built successfully with the actual local runtime files
present. An in-image Python probe confirmed an empty, writable `/app/var` and UID
10001. No image was pushed. Existing final-image dependency findings remain open;
this change does not waive the HIGH/CRITICAL vulnerability gate. Images built before
this change should be rebuilt from a clean context before any release.

Engineering prompt: keep local object storage outside every image build context;
verify with a synthetic sentinel and the final unprivileged image. Never inspect or
publish customer runtime files to demonstrate exclusion.
