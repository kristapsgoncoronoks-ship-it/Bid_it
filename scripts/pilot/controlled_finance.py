#!/usr/bin/env python3
"""Isolated synthetic finance pilot; private artifacts, explicit server cgroup caps.

Run with backend/.venv/bin/python; Docker and local Playwright dependencies needed.
Never points at an existing database, sends mail, or publishes a service externally.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from tests.factories.transport import synthetic_vat_id

POSTGRES = "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
BACKEND = os.environ.get("PILOT_BACKEND_IMAGE", "bidit-pilot-backend:review")
FRONTEND = os.environ.get("PILOT_FRONTEND_IMAGE", "bidit-pilot-frontend:review")
BRIDGE_PORT = int(os.environ.get("PILOT_BRIDGE_PORT", "8002"))
LAST_DOCKER_STDERR = b""
HTTP_CLIENT = """
import base64,json,sys,time,urllib.request,urllib.error
p=json.load(sys.stdin)
started=time.perf_counter()
h={k:v for k,v in p['headers'].items() if k.lower() not in {'connection','content-length','transfer-encoding'}}
r=urllib.request.Request('http://frontend'+p['path'],data=base64.b64decode(p['body']) if p['body'] else None,headers=h,method=p['method'])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try: response=opener.open(r,timeout=45)
except urllib.error.HTTPError as error: response=error
with response:
 print(json.dumps({'status':response.status,'headers':dict(response.headers),'body':base64.b64encode(response.read()).decode(),'elapsed_ms':round((time.perf_counter()-started)*1000,3)}))
"""


def docker(*args, payload=None, check=True):
    global LAST_DOCKER_STDERR
    result = subprocess.run(
        ["docker", *args],
        input=payload,
        capture_output=True,
        check=False,
        env={**os.environ, "DOCKER_CONFIG": "/workspace/.cache/docker"},
    )
    if check and result.returncode:
        LAST_DOCKER_STDERR = result.stderr
        # Do not echo commands/env/request data: those can contain synthetic credentials.
        raise RuntimeError(
            f"Docker operation {args[0]} failed (exit {result.returncode}); private stderr retained by caller"
        )
    return result.stdout


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2)


class Pilot:
    def __init__(self):
        global BACKEND, FRONTEND
        dirty = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "backend",
                "frontend",
            ],
            cwd=ROOT,
            text=True,
        )
        if dirty.strip():
            raise RuntimeError(
                "Commit application changes before building and running the pilot"
            )
        BACKEND = json.loads(docker("image", "inspect", BACKEND))[0]["Id"]
        FRONTEND = json.loads(docker("image", "inspect", FRONTEND))[0]["Id"]
        self.run_id = "bidit-pilot-" + uuid.uuid4().hex[:10]
        self.output = Path("/workspace/.cache") / self.run_id
        self.output.mkdir(mode=0o700)
        self.names = []
        self.volumes = []
        self.network = self.run_id
        self.db_admin = secrets.token_urlsafe(32)
        self.db_password = secrets.token_urlsafe(32)
        self.signing_key = secrets.token_urlsafe(48)
        self.owner_password = secrets.token_urlsafe(24)
        self.env = {
            "ENVIRONMENT": "development",
            "DATABASE_URL": f"postgresql+asyncpg://pilot_app:{self.db_password}@db:5432/pilot",
            "SECRET_KEY": self.signing_key,
            "JWT_SIGNING_KEY": self.signing_key,
            "STORAGE_BACKEND": "local",
            "STORAGE_LOCAL_PATH": "/data",
            "STORAGE_LOCAL_SHARED": "true",
            "SMTP_HOST": "",
            "BILLING_PROVIDER": "none",
            "WEB_CONCURRENCY": "1",
        }
        self.report = {
            "source_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "synthetic_only": True,
            "network": "private Docker internal; browser bridge loopback only",
            "profile": {
                "server_cpu_limit_sum": 2.0,
                "server_memory_limit_mib_sum": 1920,
                "swap": False,
                "api_workers": 1,
                "capture_workers": 1,
                "simultaneous_user_workflows": 1,
                "authenticated_identities": 2,
                "parallel_browser_xhr": "enabled",
                "not_a_production_sizing_guarantee": True,
            },
            "steps": [],
        }
        self.state = {
            "base": f"http://127.0.0.1:{BRIDGE_PORT}",
            "output": str(self.output),
        }
        self.bridge = None

    def step(self, name):
        self.report["steps"].append(name)
        print(name, flush=True)

    def volume(self, suffix, uid):
        name = self.run_id + "-" + suffix
        docker("volume", "create", "--label", f"bidit.pilot={self.run_id}", name)
        info = json.loads(docker("volume", "inspect", name))[0]
        if info.get("Labels", {}).get("bidit.pilot") != self.run_id:
            raise RuntimeError("Refusing a volume not owned by this pilot")
        self.volumes.append(name)
        docker(
            "run",
            "--rm",
            "--user",
            "0",
            "--cap-drop=ALL",
            "--cap-add=CHOWN",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"source={name},target=/data",
            "--entrypoint",
            "sh",
            POSTGRES,
            "-c",
            f"chown {uid}:{uid} /data",
        )
        return name

    def start(
        self,
        suffix,
        image,
        cpu,
        memory,
        *,
        alias=None,
        mount=None,
        env=None,
        command=(),
        entrypoint=None,
        user=None,
    ):
        name = self.run_id + "-" + suffix
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"bidit.pilot={self.run_id}",
            "--network",
            self.network,
            "--cpus",
            str(cpu),
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--pids-limit",
            "128",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
        ]
        if alias:
            args += ["--network-alias", alias]
        if mount:
            args += [
                "--mount",
                f"source={mount},target={'/var/lib/postgresql/data' if suffix.endswith('db') else '/data'}",
            ]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        if entrypoint:
            args += ["--entrypoint", entrypoint]
        if user:
            args += ["--user", str(user)]
        args += [image, *command]
        docker(*args)
        self.names.append(name)
        return name

    def wait_db(self, name):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if (
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        name,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "pilot_admin",
                        "-d",
                        "postgres",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            ):
                return
            time.sleep(0.2)
        raise RuntimeError("Synthetic database readiness failed")

    def sql(self, container, statement):
        return docker(
            "exec",
            "-i",
            container,
            "psql",
            "-U",
            "pilot_admin",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            payload=statement.encode(),
        )

    def exec_python(self, container, code, data=None):
        # Program is argv; private request data is stdin, never argv/log output.
        output = docker(
            "exec",
            "-i",
            container,
            "python",
            "-c",
            code,
            payload=json.dumps(data).encode() if data is not None else None,
        )
        return json.loads(output) if output else None

    def bridge_start(self, control):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                response = outer.exec_python(
                    control,
                    HTTP_CLIENT,
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": base64.b64encode(body).decode(),
                    },
                )
                if self.path.startswith("/api/") and response["status"] < 400:
                    outer.report.setdefault("origin_api_ms", []).append(
                        response["elapsed_ms"]
                    )
                content = base64.b64decode(response["body"])
                self.send_response(response["status"])
                for key, value in response["headers"].items():
                    if key.lower() not in {
                        "content-length",
                        "transfer-encoding",
                        "connection",
                    }:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                try:
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = request
            do_POST = request
            do_PUT = request
            do_PATCH = request
            do_DELETE = request

        self.bridge = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), Handler)
        threading.Thread(target=self.bridge.serve_forever, daemon=True).start()

    def api(
        self, method, path, body=None, token=None, raw=None, headers=None, expected=200
    ):
        outgoing = dict(headers or {})
        if token:
            outgoing["Authorization"] = "Bearer " + token
        if body is not None:
            raw = json.dumps(body).encode()
            outgoing["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.state["base"] + "/api/v1" + path,
            method=method,
            data=raw,
            headers=outgoing,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        start = time.perf_counter()
        try:
            response = opener.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            if response.status != expected:
                # Save bounded response privately; do not print possible tokens/source identifiers.
                (self.output / "last-error.bin").write_bytes(data[:65536])
                raise RuntimeError(
                    f"Pilot action {method} {path.split('?')[0]} returned {response.status}, expected {expected}"
                )
            self.report.setdefault("request_ms", []).append(
                round((time.perf_counter() - start) * 1000, 2)
            )
            return (
                json.loads(data)
                if response.headers.get_content_type() == "application/json"
                else data
            )

    def multipart(
        self, path, filename, content, mime, token, expected=202, extra_headers=None
    ):
        boundary = "synthetic-" + uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self.api(
            "POST",
            path,
            token=token,
            raw=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                **(extra_headers or {}),
            },
            expected=expected,
        )

    def setup(self):
        docker(
            "network",
            "create",
            "--internal",
            "--label",
            f"bidit.pilot={self.run_id}",
            self.network,
        )
        self.objects = self.volume("objects", 10001)
        db_volume = self.volume("db-data", 70)
        self.db = self.start(
            "db",
            POSTGRES,
            0.5,
            "512m",
            alias="db",
            mount=db_volume,
            user="70",
            env={
                "POSTGRES_USER": "pilot_admin",
                "POSTGRES_PASSWORD": self.db_admin,
                "POSTGRES_DB": "postgres",
                "POSTGRES_INITDB_ARGS": "--data-checksums",
            },
        )
        self.wait_db(self.db)
        self.sql(
            self.db,
            f"CREATE ROLE pilot_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{self.db_password}';\nCREATE DATABASE pilot OWNER pilot_app;\n",
        )
        migrator = self.start(
            "migrator",
            BACKEND,
            0.6,
            "512m",
            mount=self.objects,
            env=self.env,
            entrypoint="sh",
            command=("-c", "alembic upgrade head"),
        )
        status = docker("wait", migrator).decode().strip()
        if status != "0":
            raise RuntimeError(
                "Synthetic migration failed; inspect this run's migrator privately"
            )
        docker("rm", migrator)
        self.api_container = self.start(
            "api",
            BACKEND,
            0.6,
            "512m",
            alias="backend",
            mount=self.objects,
            env=self.env,
        )
        self.control = self.start(
            "control", BACKEND, 1, "256m", entrypoint="sleep", command=("infinity",)
        )
        self.front = self.start("frontend", FRONTEND, 0.2, "128m", alias="frontend")
        self.bridge_start(self.control)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                if self.api("GET", "/auth/me", expected=401):
                    break
            except (RuntimeError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("Synthetic API readiness failed")
        self.report["images"] = {
            name: json.loads(docker("image", "inspect", image))[0]["Id"]
            for name, image in (
                ("backend", BACKEND),
                ("frontend", FRONTEND),
                ("postgres", POSTGRES),
            )
        }
        backend_info = json.loads(docker("image", "inspect", BACKEND))[0]
        frontend_info = json.loads(docker("image", "inspect", FRONTEND))[0]
        trees = {
            "backend": backend_info["Config"]["Labels"]["org.bidit.backend-tree"],
            "frontend": frontend_info["Config"]["Labels"]["org.bidit.frontend-tree"],
        }
        for directory, digest in trees.items():
            expected = subprocess.check_output(
                ["git", "rev-parse", f"HEAD:{directory}"], cwd=ROOT, text=True
            ).strip()
            assert digest == expected, (
                "Pilot image does not match the current application tree"
            )
        self.report["application_trees"] = trees
        self.report["image_source_revisions"] = {
            "backend": backend_info["Config"]["Labels"][
                "org.opencontainers.image.revision"
            ],
            "frontend": frontend_info["Config"]["Labels"][
                "org.opencontainers.image.revision"
            ],
        }
        self.step("Private PostgreSQL/API/frontend ready under explicit caps")

    def prepare(self):
        owner_email = f"pilot-owner-{self.run_id}@example.com"
        registration = self.api(
            "POST",
            "/auth/register",
            {
                "organization_name": "Synthetic controlled finance",
                "name": "Synthetic Owner",
                "email": owner_email,
                "password": self.owner_password,
            },
            expected=201,
        )
        self.state["owner_token"] = registration["token"]["access_token"]
        token = self.state["owner_token"]
        me = self.api("GET", "/auth/me", token=token)
        self.state["org_id"] = me["organization"]["id"]
        issuer = self.api(
            "PUT",
            "/issuer",
            {
                "legal_name": "Synthetic Pilot Company",
                "vat_number": synthetic_vat_id(seed=1605),
                "address_line1": "Synthetic street 1",
                "city": "Tallinn",
                "postal_code": "10111",
                "country": "EE",
                "invoice_prefix": "SYNTH-",
            },
            token,
        )
        self.state["entity_id"] = issuer["id"]
        self.api("PUT", "/modules/issuing", {"enabled": True}, token)
        invite = self.api(
            "POST",
            "/team/invites",
            {"email": f"pilot-approver-{self.run_id}@example.com", "role": "admin"},
            token,
            expected=201,
        )
        approver = self.api(
            "POST",
            "/auth/accept-invite",
            {
                "token": invite["token"],
                "name": "Synthetic Approver",
                "password": secrets.token_urlsafe(24),
            },
            expected=201,
        )
        self.state["approver_token"] = approver["token"]["access_token"]
        source = b"vendor,invoice_number,issue_date,description,category,quantity,unit_price,tax_rate\nSynthetic Supplier,SYNTH-AP-1605,2026-10-16,Synthetic service,other,1,100.00,21\n"
        accepted = self.multipart(
            "/invoices/upload", "synthetic-supplier.csv", source, "text/csv", token
        )
        self.state["capture_id"] = accepted["extraction_run_id"]
        fault_code = """
import asyncio,json
from datetime import UTC,datetime,timedelta
from app.core.database import SessionLocal
from app.services import jobs
async def main():
 async with SessionLocal() as db:
  job=await jobs.claim(db,'synthetic-disappeared-worker',kinds=('upload.extract',))
  assert job is not None
  job_id=job.id
  reclaimed=await jobs.reclaim_stale(db,now=datetime.now(UTC)+timedelta(seconds=jobs.STALE_LEASE_SECONDS+1))
  assert reclaimed==1
  print(json.dumps({'job_id':job_id,'lease_reclaimed':True}))
asyncio.run(main())
"""
        fault = self.exec_python(self.api_container, fault_code)
        recovered = self.api("POST", f"/jobs/{fault['job_id']}/retry", token=token)
        assert recovered["status"] == "queued"
        self.report["fault_recovery"] = {
            "native_claim_and_reclaim": True,
            "lease_expiry_clock_argument_simulated": True,
            "physical_worker_sigkill": False,
            "operator_retry_api": True,
            "service_deadlines_unchanged": True,
        }
        self.worker = self.start(
            "worker",
            BACKEND,
            0.7,
            "768m",
            mount=self.objects,
            env=self.env,
            entrypoint="python",
            command=("-m", "app.worker", "--kinds", "upload.extract"),
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = self.api(
                "GET", f"/invoices/upload/{self.state['capture_id']}", token=token
            )
            if result["status"] == "parsed":
                break
            if result["status"] == "failed":
                raise RuntimeError(
                    "Synthetic source capture failed; inspect private source state"
                )
            time.sleep(0.2)
        else:
            raise RuntimeError("Synthetic capture did not complete")
        self.state["source_sha256"] = hashlib.sha256(source).hexdigest()
        self.step("Real native worker parsed the stored synthetic supplier source")
        private_json(self.output / "state.json", self.state)

    def resources(self, names):
        result = {}
        for name in names:
            fields = (
                "memory.peak",
                "memory.max",
                "memory.swap.max",
                "memory.events",
                "cpu.max",
                "cpu.stat",
            )
            values = {
                field: docker("exec", name, "cat", "/sys/fs/cgroup/" + field)
                .decode()
                .strip()
                for field in fields
            }
            config = json.loads(docker("inspect", name))[0]["HostConfig"]
            values["limits"] = {
                key: config[key]
                for key in (
                    "NanoCpus",
                    "Memory",
                    "MemorySwap",
                    "CapDrop",
                    "SecurityOpt",
                )
            }
            assert int(values["memory.max"]) == config["Memory"]
            assert int(values["memory.swap.max"]) == 0
            quota, period = map(int, values["cpu.max"].split())
            assert quota * 1_000_000_000 // period == config["NanoCpus"]
            status = docker("exec", name, "cat", "/proc/1/status").decode()
            values["runtime_identity"] = {
                line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                for line in status.splitlines()
                if line.startswith(("Uid:", "CapEff:", "NoNewPrivs:"))
            }
            assert values["runtime_identity"]["Uid"].split()[0] != "0"
            assert int(values["runtime_identity"]["CapEff"], 16) == 0
            assert values["runtime_identity"]["NoNewPrivs"] == "1"
            result[name.split(self.run_id + "-")[-1]] = values
        return result

    def financial_snapshot(self):
        code = """
import asyncio,json
from dataclasses import asdict
from sqlalchemy import select,func,text
from app.core.database import SessionLocal
from app.models import Base
from app.services import audit
async def main():
 async with SessionLocal() as db:
  role=(await db.execute(text('select rolsuper,rolbypassrls from pg_roles where rolname=current_user'))).one()
  assert role == (False,False)
  tables=('invoices','issued_invoices','payments','bank_lines','extraction_runs','issued_invoice_attachments','audit_events')
  counts={name:await db.scalar(select(func.count()).select_from(Base.metadata.tables[name])) for name in tables}
  org_id=await db.scalar(select(Base.metadata.tables['organizations'].c.id))
  chain=asdict(await audit.verify_chain(db,org_id));assert chain['ok']
  links=await db.scalar(text("SELECT count(*) FROM issued_invoices i WHERE i.doc_type='credit_note' AND EXISTS (SELECT 1 FROM audit_events a WHERE a.action='issued.credit_note' AND a.target_id=i.id::text)"))
  notes=await db.scalar(text("SELECT count(*) FROM issued_invoices WHERE doc_type='credit_note'"))
  assert links==notes==1
  chain['credit_document_links_verified']=True
  print(json.dumps({'counts':counts,'application_superuser':role[0],'application_bypassrls':role[1],'audit_chain':chain}))
asyncio.run(main())
"""
        return self.exec_python(self.api_container, code)

    def fault_and_restore(self, browser):
        drill_start = time.perf_counter()
        self.state = json.loads((self.output / "state.json").read_text())
        token = self.state["owner_token"]
        attachment = f"/issued/{self.state['issued_id']}/attachments/{self.state['attachment_id']}/download"
        before = self.api("GET", attachment, token=token)
        assert hashlib.sha256(before).hexdigest() == self.state["attachment_sha256"]
        snapshot = self.financial_snapshot()
        self.report["financial_snapshot_before_restore"] = snapshot
        self.report["cgroups_after_browser"] = self.resources(
            [self.api_container, self.worker, self.db, self.front]
        )
        # Quiesce every writer before taking both halves of the logical backup.
        docker("stop", self.worker, self.api_container, self.front)
        dump = docker(
            "exec", self.db, "pg_dump", "-U", "pilot_admin", "-d", "pilot", "-Fc"
        )
        archive = docker(
            "run",
            "--rm",
            "--user",
            "0",
            "--cap-drop=ALL",
            "--mount",
            f"source={self.objects},target=/data,readonly",
            "--entrypoint",
            "tar",
            POSTGRES,
            "-cf",
            "-",
            "-C",
            "/data",
            ".",
        )
        for name, content in (("database.dump", dump), ("objects.tar", archive)):
            descriptor = os.open(
                self.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
        self.report["backup"] = {
            "consistent_writers_stopped": True,
            "database_bytes": len(dump),
            "database_sha256": hashlib.sha256(dump).hexdigest(),
            "objects_bytes": len(archive),
            "objects_sha256": hashlib.sha256(archive).hexdigest(),
        }
        # A deliberate source fault must be refused, then repaired by restore.
        sha = self.state["attachment_sha256"]
        org = self.state["org_id"]
        key = f"issued-attachments/{org}/{sha[:2]}/{sha[2:4]}/{sha}"
        damage = "import pathlib,sys; p=pathlib.Path('/data')/sys.argv[1]; assert p.is_file(); p.write_bytes(b'Synthetic injected corruption')"
        docker(
            "run",
            "--rm",
            "--mount",
            f"source={self.objects},target=/data",
            "--entrypoint",
            "python",
            BACKEND,
            "-c",
            damage,
            key,
        )
        docker("start", self.api_container, self.front)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                error = self.api("GET", attachment, token=token, expected=503)
                if "integrity" not in error["detail"].lower():
                    raise RuntimeError("Fault was not an integrity refusal")
                break
            except (RuntimeError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("Source corruption did not fail closed")
        docker("stop", self.api_container, self.front, self.db)
        # Remove only this run's stopped endpoints, preventing stale DNS aliases.
        docker("rm", self.api_container, self.db, self.worker)
        restored_db_volume = self.volume("restored-db-data", 70)
        restored_objects = self.volume("restored-objects", 10001)
        docker(
            "run",
            "--rm",
            "-i",
            "--user",
            "10001",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"source={restored_objects},target=/data",
            "--entrypoint",
            "tar",
            BACKEND,
            "--no-same-owner",
            "-xf",
            "-",
            "-C",
            "/data",
            payload=archive,
        )
        self.db = self.start(
            "restored-db",
            POSTGRES,
            0.5,
            "512m",
            alias="db",
            mount=restored_db_volume,
            user="70",
            env={
                "POSTGRES_USER": "pilot_admin",
                "POSTGRES_PASSWORD": self.db_admin,
                "POSTGRES_DB": "postgres",
                "POSTGRES_INITDB_ARGS": "--data-checksums",
            },
        )
        self.wait_db(self.db)
        self.sql(
            self.db,
            f"CREATE ROLE pilot_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{self.db_password}';\nCREATE DATABASE pilot OWNER pilot_app;\n",
        )
        restore_start = time.perf_counter()
        docker(
            "exec",
            "-i",
            self.db,
            "pg_restore",
            "-U",
            "pilot_admin",
            "-d",
            "pilot",
            "--exit-on-error",
            payload=dump,
        )
        self.api_container = self.start(
            "restored-api",
            BACKEND,
            0.6,
            "512m",
            alias="backend",
            mount=restored_objects,
            env=self.env,
        )
        docker("start", self.front)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                self.api("GET", "/auth/me", token=token)
                break
            except (RuntimeError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("Restored API did not become ready")
        restored = self.financial_snapshot()
        assert (
            restored["counts"] == snapshot["counts"]
            and restored["audit_chain"] == snapshot["audit_chain"]
        )
        original = self.api("GET", attachment, token=token)
        assert original == before and hashlib.sha256(original).hexdigest() == sha
        source = self.api(
            "GET", f"/invoices/captures/{self.state['capture_id']}/source", token=token
        )
        assert hashlib.sha256(source).hexdigest() == self.state["source_sha256"]
        invoice = self.api("GET", f"/issued/{self.state['issued_id']}", token=token)
        assert (
            invoice["total"],
            invoice["credited_total"],
            invoice["amount_paid"],
            invoice["outstanding"],
        ) == ("100.00", "10.00", "90.00", "0.00")
        bank = self.api(
            "GET",
            f"/reconciliation/statements/{self.state['statement_id']}/lines",
            token=token,
        )
        assert (
            len(bank) == 1
            and bank[0]["matched_id"] == self.state["payment_id"]
            and bank[0]["currency"] == "EUR"
        )
        csv = self.api(
            "POST",
            "/export/accounting/download",
            {"token": self.state["export_token"]},
            token=token,
        )
        assert hashlib.sha256(csv).hexdigest() == self.state["csv_sha256"]
        replay = self.api(
            "POST",
            f"/issued/{self.state['issued_id']}/payments",
            self.state["cash"],
            token,
            headers={"Idempotency-Key": "synthetic-pilot-payment-command"},
        )
        assert replay["payment"]["id"] == self.state["payment_id"]
        credit = self.api(
            "POST",
            f"/issued/{self.state['issued_id']}/credit-note",
            self.state["credit_payload"],
            token,
            headers={"Idempotency-Key": "synthetic-pilot-credit-command"},
        )
        assert credit["id"] == self.state["credit_id"]
        self.report["restore"] = {
            "separate_database_and_object_volume": True,
            "restore_and_verification_after_db_bootstrap_seconds": round(
                time.perf_counter() - restore_start, 3
            ),
            "total_drill_seconds": round(time.perf_counter() - drill_start, 3),
            "row_counts_and_audit_chain_preserved": True,
            "source_and_attachment_hashes_preserved": True,
            "accounting_snapshot_hash_preserved": True,
            "payment_and_credit_replay_ids_preserved": True,
            "injected_corruption_was_refused": True,
        }
        self.worker = self.start(
            "restored-worker",
            BACKEND,
            0.7,
            "768m",
            mount=restored_objects,
            env=self.env,
            entrypoint="python",
            command=("-m", "app.worker", "--kinds", "upload.extract"),
        )
        after_source = b"vendor,invoice_number,issue_date,description,quantity,unit_price,tax_rate\nSynthetic Supplier,SYNTH-AFTER-RESTORE,2026-10-16,Restored worker verification,1,1.00,0\n"
        accepted = self.multipart(
            "/invoices/upload",
            "synthetic-after-restore.csv",
            after_source,
            "text/csv",
            token,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            resumed = self.api(
                "GET", f"/invoices/upload/{accepted['extraction_run_id']}", token=token
            )
            if resumed["status"] == "parsed":
                break
            if resumed["status"] == "failed":
                raise RuntimeError(
                    "Restored worker could not parse the new synthetic source"
                )
            time.sleep(0.2)
        else:
            raise RuntimeError("Restored worker did not complete new queued work")
        self.report["restore"]["native_worker_processed_new_capture"] = True
        self.report["browser"] = browser
        self.report["cgroups_after_restore"] = self.resources(
            [self.api_container, self.db, self.front]
        )
        for sample in (
            self.report["cgroups_after_browser"],
            self.report["cgroups_after_restore"],
        ):
            for values in sample.values():
                events = values.get(
                    "memory.events", values.get("raw_cgroup_counters", "")
                )
                for line in events.splitlines():
                    if line.startswith(("oom ", "oom_kill ")):
                        assert line.split()[1] == "0"
        self.report["oom_events"] = 0
        self.step(
            "Consistent database/object backup restored into separate volumes; byte, ledger and audit checks passed"
        )

    def cleanup(self):
        if self.bridge:
            self.bridge.shutdown()
            self.bridge.server_close()
        for name in reversed(self.names):
            details = docker("inspect", name, check=False)
            items = json.loads(details) if details else []
            if items:
                item = items[0]
                if item["Config"].get("Labels", {}).get("bidit.pilot") == self.run_id:
                    docker("rm", "-f", name, check=False)
        for name in self.volumes:
            docker("volume", "rm", name, check=False)
        network = docker("network", "inspect", self.network, check=False)
        if (
            network
            and json.loads(network)[0].get("Labels", {}).get("bidit.pilot")
            == self.run_id
        ):
            docker("network", "rm", self.network, check=False)

    def run(self):
        try:
            self.setup()
            self.prepare()
            # Browser receives only a private state path; no credentials in argv/logs.
            self.report["private_artifact_directory"] = str(self.output)
            self.report["cgroups_before_browser"] = self.resources(
                [self.api_container, self.worker, self.db, self.front]
            )
            private_json(self.output / "report.json", self.report)
            private_json(
                self.output / "controller.json",
                {
                    "containers": self.names,
                    "volumes": self.volumes,
                    "network": self.network,
                },
            )
            self.step("Pilot fixture ready; starting real browser workflow")
            environment = {**os.environ, "PW_CHROMIUM_PATH": "/usr/bin/chromium"}
            browser_start = time.perf_counter()
            browser = subprocess.run(
                [
                    "node",
                    str(ROOT / "scripts/pilot/browser_finance.mjs"),
                    str(self.output / "state.json"),
                ],
                capture_output=True,
                check=False,
                env=environment,
            )
            for name, content in (
                ("browser.stdout", browser.stdout),
                ("browser.stderr", browser.stderr),
            ):
                descriptor = os.open(
                    self.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
            if browser.returncode:
                raise RuntimeError(
                    "Pilot browser workflow failed; private browser.stderr retained"
                )
            self.report["browser_workflow_seconds"] = round(
                time.perf_counter() - browser_start, 3
            )
            self.fault_and_restore(
                json.loads((self.output / "browser.json").read_text())
            )
            private_json(self.output / "report.json", self.report)
            print(str(self.output), flush=True)
        except Exception as error:
            for name in self.names:
                logs = subprocess.run(
                    ["docker", "logs", "--tail", "150", name],
                    capture_output=True,
                    check=False,
                )
                for suffix, content in (
                    ("stdout", logs.stdout),
                    ("stderr", logs.stderr),
                ):
                    descriptor = os.open(
                        self.output / (name + "." + suffix),
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                        0o600,
                    )
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
            self.report["failure"] = {
                "type": type(error).__name__,
                "detail": str(error),
            }
            private_json(self.output / "report.json", self.report)
            if LAST_DOCKER_STDERR:
                descriptor = os.open(
                    self.output / "docker.stderr",
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(LAST_DOCKER_STDERR)
            raise
        finally:
            self.cleanup()


if __name__ == "__main__":
    Pilot().run()
