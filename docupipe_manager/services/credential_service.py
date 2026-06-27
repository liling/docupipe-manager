import asyncio
import binascii
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from tempfile import mkdtemp

from docupipe_manager.services.dws_env import isolated_dws_env, make_dws_env

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.crypto import decrypt_sm4, encrypt_sm4
from docupipe_manager.models.dws_credential import CredentialStatus, DwsCredential
from docupipe_manager.models.job import Job, JobKind, JobStatus, JobTriggerType
from docupipe_manager.models.task import CredentialType
from docupipe_manager.platform.client import XinyiPlatformClient

logger = logging.getLogger(__name__)


class CredentialError(Exception):
    pass


def _parse_dt(s: str | None) -> datetime | None:
    """宽松解析 ISO 8601 字符串（兼容 'Z' 后缀）；失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class CredentialService:
    """Manage dws credential lifecycle via device flow."""

    def __init__(self, engine: AsyncEngine, settings: Settings, platform_client: XinyiPlatformClient):
        self._engine = engine
        self._settings = settings
        self._platform_client = platform_client
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._active_sessions: dict[str, dict] = {}

    async def start_device_login(self, project_id: uuid.UUID, name: str) -> dict:
        """Start dws auth login --device, return verification_url + user_code + session_key."""
        session_key = uuid.uuid4().hex
        root = mkdtemp(prefix="dws-device-")
        env = make_dws_env(root)

        proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, "auth", "login", "--device",
            "--format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=root,
        )

        try:
            first_chunk = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            info = json.loads(first_chunk)
        except Exception as e:
            proc.kill()
            shutil.rmtree(root, ignore_errors=True)
            raise ValueError(f"Failed to start device login: {e}") from e

        self._active_sessions[session_key] = {
            "proc": proc,
            "root": root,
            "env": env,
            "name": name,
            "project_id": project_id,
            "created_at": time.monotonic(),
        }

        return {"session_key": session_key, **info}

    async def poll_device_login(self, session_key: str) -> dict:
        """Check device login status. Returns {"status": "pending" | "success" | "failed"}."""
        session = self._active_sessions.get(session_key)
        if session is None:
            return {"status": "failed", "error": "Session not found"}

        proc = session["proc"]
        retcode = proc.returncode
        if retcode is None:
            return {"status": "pending"}

        if retcode != 0:
            self._cleanup_session(session_key)
            return {"status": "failed", "error": f"dws exited with code {retcode}"}

        stdout, _ = await proc.communicate()
        try:
            result = json.loads(stdout.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._cleanup_session(session_key)
            return {"status": "failed", "error": "Failed to parse dws output"}

        session["result"] = result
        return {"status": "success", "result": result}

    async def finalize_login(self, session_key: str, name: str, user_id: uuid.UUID, project_id: uuid.UUID) -> DwsCredential:
        """Complete login: read dws status, export auth blob, SM4 encrypt, store in DB."""
        session = self._active_sessions.get(session_key)
        if session is None:
            raise ValueError("Session not found or expired")
        env = session["env"]
        root = session["root"]

        try:
            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await status_proc.communicate()
            status_data = json.loads(stdout.decode()) if stdout else {}
            corp_id = status_data.get("corp_id", "")
            token_expires_at_str = status_data.get("expires_at")
            refresh_expires_at_str = status_data.get("refresh_expires_at")

            export_path = os.path.join(root, "dws-export.b64")
            export_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "export", "--base64", "-o", export_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await export_proc.communicate()
            if export_proc.returncode != 0 or not os.path.exists(export_path):
                raise ValueError("dws auth export failed")

            with open(export_path, "r") as f:
                auth_b64 = f.read().strip()

            key_hex = self._settings.encryption_key
            auth_blob_hex = encrypt_sm4(auth_b64, key_hex)
        except Exception:
            self._cleanup_session(session_key)
            raise

        credential = DwsCredential(
            name=name,
            corp_id=corp_id,
            auth_blob=bytes.fromhex(auth_blob_hex),
            token_expires_at=_parse_dt(token_expires_at_str),
            refresh_token_expires_at=_parse_dt(refresh_expires_at_str),
            credential_type=CredentialType.dws,
            status=CredentialStatus.active,
            created_by=user_id,
            project_id=project_id,
        )

        async with self._session_factory() as db_session:
            db_session.add(credential)
            await db_session.commit()
            await db_session.refresh(credential)

        self._cleanup_session(session_key)

        asyncio.create_task(self._platform_client.push_audit({
            "event": "docupipe.credential.create",
            "credential_id": str(credential.id),
            "name": name,
        }))

        return credential

    async def create_from_import(
        self, project_id: uuid.UUID, name: str, auth_b64: str, user_id: uuid.UUID
    ) -> DwsCredential:
        """方式 A：用户粘贴/上传 dws auth export 的 base64，import+status 验证后加密存储。"""
        meta = await self._probe_auth_blob(auth_b64)

        key_hex = self._settings.encryption_key
        auth_blob_hex = encrypt_sm4(auth_b64, key_hex)

        credential = DwsCredential(
            name=name,
            corp_id=meta.get("corp_id", ""),
            auth_blob=bytes.fromhex(auth_blob_hex),
            token_expires_at=_parse_dt(meta.get("expires_at")),
            refresh_token_expires_at=_parse_dt(meta.get("refresh_expires_at")),
            credential_type=CredentialType.dws,
            status=CredentialStatus.active,
            created_by=user_id,
            project_id=project_id,
        )

        async with self._session_factory() as db_session:
            db_session.add(credential)
            await db_session.commit()
            await db_session.refresh(credential)

        asyncio.create_task(self._platform_client.push_audit({
            "event": "docupipe.credential.create",
            "credential_id": str(credential.id),
            "name": name,
            "source": "import",
        }))
        return credential

    async def _probe_auth_blob(self, auth_b64: str) -> dict:
        """import base64 凭证到隔离 env，调 status 返回元数据。
        base64 非法 / import 失败抛 ValueError。"""
        try:
            binascii.a2b_base64(auth_b64.encode("utf-8"))
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"auth_blob 不是合法的 base64: {e}") from e

        with isolated_dws_env() as env:
            import_path = os.path.join(env["HOME"], "auth.b64")
            with open(import_path, "w") as f:
                f.write(auth_b64)

            import_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "import", "--base64", "-i", import_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(import_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                import_proc.kill()
                raise ValueError("dws auth import 超时")
            if import_proc.returncode != 0:
                detail = stderr.decode().strip() if stderr else ""
                msg = "dws auth import 失败：凭证无效"
                if detail:
                    msg += f"（{detail}）"
                raise ValueError(msg)

            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(status_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                status_proc.kill()
                raise ValueError("dws auth status 超时")
            return json.loads(stdout.decode()) if stdout else {}

    async def _run_dws(self, args: list[str], env: dict[str, str] | None = None,
                       log_path: str | None = None,
                       timeout: float = 120.0) -> tuple[int, bytes, bytes]:
        kwargs: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if env is not None:
            kwargs["env"] = env
        proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, *args, **kwargs,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        if log_path:
            try:
                with open(log_path, "a") as f:
                    f.write(stdout.decode("utf-8", "replace"))
                    f.write(stderr.decode("utf-8", "replace"))
            except OSError:
                pass
        return proc.returncode, stdout, stderr

    async def check_status(self, credential_id: uuid.UUID, project_id: uuid.UUID) -> dict:
        """测试凭证可用性并回写最新 corp_id/过期时间/status。"""
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")
            key_hex = self._settings.encryption_key
            auth_b64 = decrypt_sm4(credential.auth_blob.hex(), key_hex)

        try:
            meta = await self._probe_auth_blob(auth_b64)
        except ValueError as e:
            async with self._session_factory() as db_session:
                credential = await db_session.get(DwsCredential, credential_id)
                credential.status = CredentialStatus.expired
                await db_session.commit()
            return {"status": "expired", "corp_id": credential.corp_id if credential else "",
                    "token_expires_at": None, "refresh_token_expires_at": None, "error": str(e)}

        corp_id = meta.get("corp_id") or ""
        token_exp = _parse_dt(meta.get("expires_at"))
        refresh_exp = _parse_dt(meta.get("refresh_expires_at"))
        now = datetime.now(timezone.utc)
        new_status = (CredentialStatus.expired
                      if (refresh_exp is not None and refresh_exp < now)
                      else CredentialStatus.active)

        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            credential.corp_id = corp_id
            if token_exp is not None:
                credential.token_expires_at = token_exp
            if refresh_exp is not None:
                credential.refresh_token_expires_at = refresh_exp
            credential.status = new_status
            await db_session.commit()

        return {"status": new_status.value, "corp_id": corp_id,
                "token_expires_at": str(token_exp) if token_exp else None,
                "refresh_token_expires_at": str(refresh_exp) if refresh_exp else None,
                "error": None}

    async def refresh_credential(self, credential_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            cred = await session.get(DwsCredential, credential_id)
            if cred is None or cred.status != CredentialStatus.active:
                return
            key_hex = self._settings.encryption_key
            auth_b64 = decrypt_sm4(cred.auth_blob.hex(), key_hex)

        log_dir = os.path.join(self._settings.data_dir, "credentials",
                               str(credential_id), "jobs")
        job = Job(
            kind=JobKind.credential_keepalive,
            status=JobStatus.pending,
            trigger_type=JobTriggerType.scheduled,
            command_text="dws wiki space list",
            credential_id=credential_id,
        )
        async with self._session_factory() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)

        log_path = os.path.join(log_dir, f"{job.id}.log")
        os.makedirs(log_dir, exist_ok=True)
        started_at = datetime.now(timezone.utc)

        try:
            with isolated_dws_env() as env:
                import_path = os.path.join(env["HOME"], "auth.b64")
                with open(import_path, "w") as f:
                    f.write(auth_b64)
                rc, _, _ = await self._run_dws(["auth", "import", "--base64", "-i", import_path],
                                               env=env, log_path=log_path)
                if rc != 0:
                    raise CredentialError(f"dws auth import failed (exit {rc})")

                async with self._session_factory() as session:
                    await session.execute(update(Job).where(Job.id == job.id).values(
                        status=JobStatus.running, started_at=started_at, log_path=log_path))
                    await session.commit()

                rc, _, _ = await self._run_dws(["wiki", "space", "list"], env=env, log_path=log_path)
                if rc != 0:
                    raise CredentialError(f"dws wiki space list failed (exit {rc})")

                rc, status_out, _ = await self._run_dws(["auth", "status", "--format", "json"],
                                                        env=env, log_path=log_path)
                meta = json.loads(status_out.decode()) if status_out else {}

                export_path = os.path.join(env["HOME"], "export.b64")
                rc, _, _ = await self._run_dws(["auth", "export", "--base64", "-o", export_path],
                                               env=env, log_path=log_path)
                if rc != 0 or not os.path.exists(export_path):
                    raise CredentialError("dws auth export failed")
                with open(export_path, "r") as f:
                    new_blob = f.read().strip()

            new_blob_hex = encrypt_sm4(new_blob, key_hex)
            token_exp = _parse_dt(meta.get("expires_at"))
            refresh_exp = _parse_dt(meta.get("refresh_expires_at"))
            async with self._session_factory() as session:
                cred = await session.get(DwsCredential, credential_id)
                cred.auth_blob = bytes.fromhex(new_blob_hex)
                if token_exp is not None:
                    cred.token_expires_at = token_exp
                if refresh_exp is not None:
                    cred.refresh_token_expires_at = refresh_exp
                cred.last_refreshed_at = datetime.now(timezone.utc)
                await session.execute(update(Job).where(Job.id == job.id).values(
                    status=JobStatus.succeeded, exit_code=0,
                    completed_at=datetime.now(timezone.utc), log_path=log_path))
                await session.commit()

            asyncio.create_task(self._platform_client.push_audit({
                "event": "docupipe.credential.refresh.success",
                "credential_id": str(credential_id), "job_id": str(job.id),
            }))
        except Exception as e:
            logger.warning("Keepalive failed for %s: %s", credential_id, e)
            try:
                async with self._session_factory() as session:
                    await session.execute(update(Job).where(Job.id == job.id).values(
                        status=JobStatus.failed, error_message=str(e)[:2048],
                        completed_at=datetime.now(timezone.utc), log_path=log_path))
                    await session.commit()
            except Exception:
                pass
            asyncio.create_task(self._platform_client.push_audit({
                "event": "docupipe.credential.refresh.fail",
                "credential_id": str(credential_id), "error": str(e)[:2048],
            }))

    async def revoke(self, credential_id: uuid.UUID, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """Mark credential as revoked (soft delete)."""
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")
            credential.status = CredentialStatus.revoked
            await db_session.commit()

        asyncio.create_task(self._platform_client.push_audit({
            "event": "docupipe.credential.revoke",
            "credential_id": str(credential_id),
        }))

    async def rename_credential(
        self, credential_id: uuid.UUID, new_name: str, project_id: uuid.UUID
    ) -> DwsCredential:
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")
            credential.name = new_name
            await db_session.commit()
            await db_session.refresh(credential)
            return credential

    def _cleanup_session(self, session_key: str) -> None:
        session = self._active_sessions.pop(session_key, None)
        if session:
            proc = session.get("proc")
            if proc and proc.returncode is None:
                proc.kill()
            shutil.rmtree(session.get("root", ""), ignore_errors=True)

    async def cleanup_expired_sessions(self) -> None:
        """Remove device login sessions older than 15 minutes."""
        now = time.monotonic()
        expired = [k for k, v in self._active_sessions.items()
                   if now - v.get("created_at", 0) > 900]
        for key in expired:
            logger.info("Cleaning up expired device session %s", key)
            self._cleanup_session(key)

    async def list_credentials(self, project_id: uuid.UUID) -> list[DwsCredential]:
        async with self._session_factory() as db_session:
            result = await db_session.execute(
                select(DwsCredential)
                .where(DwsCredential.project_id == project_id)
                .where(DwsCredential.status != CredentialStatus.revoked)
                .order_by(DwsCredential.created_at.desc())
            )
            return list(result.scalars().all())
