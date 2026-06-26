from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from docupipe_manager.config import Settings
    from docupipe_manager.services.runner_service import RunnerService
    from docupipe_manager.services.scheduler_service import SchedulerService
    from docupipe_manager.services.credential_service import CredentialService
    from docupipe_manager.platform.client import XinyiPlatformClient
    from docupipe_manager.platform.cache import UserLRUCache

_state: dict = {}

def init(*, engine, settings, runner, scheduler, credential, platform_client, user_cache):
    _state.update(
        engine=engine,
        settings=settings,
        runner=runner,
        scheduler=scheduler,
        credential=credential,
        platform_client=platform_client,
        user_cache=user_cache,
    )

def get_engine():
    return _state["engine"]

def get_settings():
    return _state["settings"]

def get_runner():
    return _state["runner"]

def get_scheduler():
    return _state["scheduler"]

def get_credential():
    return _state["credential"]

def get_platform_client():
    return _state["platform_client"]

def get_user_cache():
    return _state["user_cache"]
