import asyncio
import binascii
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from tempfile import mkdtemp

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.crypto import decrypt_sm4, encrypt_sm4
from docupipe_manager.models.dws_credential import CredentialStatus, DwsCredential
from docupipe_manager.models.task import CredentialType
from sqlalchemy import not_
from docupipe_manager.platform.client import XinyiPlatformClient

logger = logging.getLogger(__name__)


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
        home_dir = mkdtemp(prefix="dws-device-")

        proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, "auth", "login", "--device",
            "--format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HOME": home_dir},
            cwd=home_dir,
        )

        try:
            first_chunk = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            info = json.loads(first_chunk)
        except Exception as e:
            proc.kill()
            shutil.rmtree(home_dir, ignore_errors=True)
            raise ValueError(f"Failed to start device login: {e}") from e

        self._active_sessions[session_key] = {
            "proc": proc,
            "home_dir": home_dir,
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
        home_dir = session["home_dir"]

        try:
            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            stdout, _ = await status_proc.communicate()
            status_data = json.loads(stdout.decode()) if stdout else {}
            corp_id = status_data.get("corp_id", "")
            token_expires_at_str = status_data.get("token_expires_at")
            refresh_expires_at_str = status_data.get("refresh_token_expires_at")

            export_path = os.path.join(home_dir, "dws-export.b64")
            export_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "export", "--base64", "-o", export_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
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

    async def _probe_auth_blob(self, auth_b64: str) -> dict:
        """把 base64 auth 写入临时 HOME，import 后调 status，返回 status 元数据。
        import 失败抛 ValueError；finally 清理临时目录。"""
        try:
            binascii.a2b_base64(auth_b64.encode("utf-8"))
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"auth_blob 不是合法的 base64: {e}") from e

        home_dir = mkdtemp(prefix="dws-probe-")
        try:
            import_path = os.path.join(home_dir, "auth.b64")
            with open(import_path, "w") as f:
                f.write(auth_b64)

            import_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "import", "-i", import_path, "--base64",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            try:
                await asyncio.wait_for(import_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                import_proc.kill()
                raise ValueError("dws auth import 超时")
            if import_proc.returncode != 0:
                raise ValueError("dws auth import 失败：凭证无效")

            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            try:
                stdout, _ = await asyncio.wait_for(status_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                status_proc.kill()
                raise ValueError("dws auth status 超时")
            return json.loads(stdout.decode()) if stdout else {}
        finally:
            shutil.rmtree(home_dir, ignore_errors=True)

    async def check_status(self, credential_id: uuid.UUID, project_id: uuid.UUID) -> dict:
        """读凭证并 import+status 探测（本任务仅返回，回写见 Task 4）。"""
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")

        key_hex = self._settings.encryption_key
        auth_b64 = decrypt_sm4(credential.auth_blob.hex(), key_hex)
        return await self._probe_auth_blob(auth_b64)

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

    def _cleanup_session(self, session_key: str) -> None:
        session = self._active_sessions.pop(session_key, None)
        if session:
            proc = session.get("proc")
            if proc and proc.returncode is None:
                proc.kill()
            shutil.rmtree(session.get("home_dir", ""), ignore_errors=True)

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
