#!/usr/bin/env python3
"""Exercise the built image's local-volume contract using owned synthetic volumes.

Usage: python3 scripts/check_storage_volume.py IMAGE
Requires Docker. Never accepts an existing volume or changes customer ownership.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid

PROBE = r"""
import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

stage = sys.argv[1]
root = Path('/app/var/storage')
original = b'synthetic original for shared-volume verification'
page = b'synthetic page for shared-volume verification'

def snapshot():
    result = {}
    for p in [root, *sorted(root.rglob('*'))]:
        s = p.stat()
        result[str(p.relative_to(root))] = {
            'uid': s.st_uid, 'gid': s.st_gid, 'mode': oct(stat.S_IMODE(s.st_mode)),
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
        }
    return result

if stage == 'image':
    assert os.getuid() == os.getgid() == 10001
    assert set(Path('/app/var').iterdir()) == {root}, 'expected only the prepared storage directory'
    assert root.is_dir() and not any(root.iterdir()), 'storage must start empty'
    s = root.stat()
    assert s.st_uid == s.st_gid == 10001 and stat.S_IMODE(s.st_mode) == 0o700
    q = Path('/app/var/write-probe')
    q.write_bytes(b'synthetic')
    q.unlink()
    print(json.dumps({'stage': stage, 'uid': os.getuid(), 'root': snapshot()}))
    sys.exit(0)

if stage in ('legacy_seed', 'legacy_inspect'):
    assert os.getuid() == 0
    if stage == 'legacy_seed':
        os.chown(root, 0, 0)
        root.chmod(0o700)
        q = root / 'operator-owned-synthetic'
        q.write_bytes(b'synthetic legacy bytes must stay unchanged')
        q.chmod(0o600)
    print(json.dumps({'stage': stage, 'snapshot': snapshot()}))
    sys.exit(0)

if stage == 'other_uid':
    assert os.getuid() == 10002
    try:
        list(root.iterdir())
    except PermissionError:
        pass
    else:
        raise AssertionError('unrelated user could list the private store')
    print(json.dumps({'stage': stage, 'refused': True}))
    sys.exit(0)

assert os.getuid() == os.getgid() == 10001
from app.core.storage import LocalStorage
from app.services import documents

store = LocalStorage(str(root))
if stage == 'legacy_refusal':
    try:
        store.put('must-not-appear', b'synthetic')
    except PermissionError:
        pass
    else:
        raise AssertionError('runtime unexpectedly changed or bypassed legacy ownership')
    print(json.dumps({'stage': stage, 'refused': True}))
    sys.exit(0)

from app.core import storage
storage.set_storage(store)
org = 'sys03-synthetic'
original_sha = hashlib.sha256(original).hexdigest()
page_sha = hashlib.sha256(page).hexdigest()
if stage == 'original':
    assert not any(root.iterdir())
    s = root.stat()
    assert s.st_uid == s.st_gid == 10001 and stat.S_IMODE(s.st_mode) == 0o700
    sha, size = asyncio.run(documents.store(documents.UPLOADS, org, original))
    assert sha == original_sha and size == len(original)
else:
    assert asyncio.run(documents.load(documents.UPLOADS, org, original_sha)) == original
    if stage == 'page':
        sha, size = asyncio.run(documents.store('capture-pages', org, page))
        assert sha == page_sha and size == len(page)
    else:
        assert asyncio.run(documents.load('capture-pages', org, page_sha)) == page
        if stage == 'readonly':
            try:
                store.put('must-not-appear', b'synthetic')
            except OSError as exc:
                assert exc.errno in (errno.EROFS, errno.EACCES)
            else:
                raise AssertionError('write to read-only storage unexpectedly succeeded')
            assert not store.exists('must-not-appear')
        elif stage == 'existing_seed':
            root.chmod(0o750)
            q = root / 'operator-preserved-synthetic'
            q.write_bytes(b'synthetic pre-existing bytes')
            q.chmod(0o400)
        else:
            assert stage == 'read'
    assert asyncio.run(documents.load(documents.UPLOADS, org, original_sha)) == original

objects = snapshot()
for name, metadata in objects.items():
    assert int(metadata['mode'], 8) & 0o002 == 0, (name, 'world-writable')
    if name.startswith(('uploads/', 'capture-pages/')) and metadata['sha256']:
        assert metadata['mode'] == '0o600'
assert not list(root.rglob('.upload-*.tmp'))
print(json.dumps({'stage': stage, 'snapshot': objects}))
"""


def main(image: str) -> None:
    prefix = f"bidit-storage-check-{uuid.uuid4().hex}"
    volumes: list[str] = []
    containers: list[str] = []
    results: list[dict] = []

    def docker(args: list[str], *, script: str | None = None) -> str:
        result = subprocess.run(
            ["docker", *args],
            input=script,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"storage check failed ({result.returncode}): {result.stderr}"
            )
        return result.stdout

    def volume(suffix: str) -> str:
        name = f"{prefix}-{suffix}"
        docker(["volume", "create", name])
        volumes.append(name)
        return name

    def probe(stage: str, mount: str | None = None, *, readonly: bool = False) -> dict:
        name = f"{prefix}-{len(containers)}"
        containers.append(name)
        args = [
            "run",
            "--rm",
            "--name",
            name,
            "--interactive",
            "--network",
            "none",
            "--no-healthcheck",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            "python",
        ]
        if mount:
            args += [
                "--read-only",
                "--mount",
                f"type=volume,src={mount},dst=/app/var/storage"
                + (",readonly" if readonly else ""),
            ]
        if stage.startswith("legacy_") and stage != "legacy_refusal":
            # Fixture-only root: initialize/inspect our new synthetic legacy volume.
            args += ["--user", "0:0", "--cap-add", "CHOWN"]
        elif stage == "other_uid":
            args += ["--user", "10002:10002"]
        result = json.loads(docker([*args, image, "-", stage], script=PROBE))
        results.append(result)
        print(f"storage-volume: {stage} passed", flush=True)
        return result

    try:
        probe("image")
        shared = volume("shared")
        probe("original", shared)
        probe("page", shared)
        before = probe("read", shared)["snapshot"]
        assert probe("readonly", shared, readonly=True)["snapshot"] == before
        probe("other_uid", shared)
        # New containers stand in for separately restarted API and worker processes.
        before = probe("existing_seed", shared)["snapshot"]
        assert probe("read", shared)["snapshot"] == before
        legacy = volume("legacy")
        before = probe("legacy_seed", legacy)["snapshot"]
        probe("legacy_refusal", legacy)
        assert probe("legacy_inspect", legacy)["snapshot"] == before
        print(json.dumps({"image": image, "checks": len(results), "results": results}))
    finally:
        for name in reversed(containers):
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=30,
                check=False,
            )
        for name in reversed(volumes):
            subprocess.run(
                ["docker", "volume", "rm", name],
                capture_output=True,
                timeout=30,
                check=False,
            )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/check_storage_volume.py IMAGE")
    main(sys.argv[1])
