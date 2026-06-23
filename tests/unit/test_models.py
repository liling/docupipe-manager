"""模型类可 import 且列映射正确（不连数据库）。"""
from docupipe_manager.models.project import Project, ProjectStatus
from docupipe_manager.models.project_member import ProjectMember
from docupipe_manager.models.task import Task, TaskStatus, CredentialType
from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus
from docupipe_manager.models.pipeline_run import PipelineRun


def test_models_importable():
    assert Project.__tablename__ == "projects"
    assert ProjectMember.__tablename__ == "project_members"
    assert Task.__tablename__ == "tasks"
    assert DwsCredential.__tablename__ == "dws_credentials"
    assert PipelineRun.__tablename__ == "pipeline_runs"


def test_enums():
    assert ProjectStatus.active.value == "active"
    assert TaskStatus.active.value == "active"
    assert CredentialType.dws.value == "dws"
    assert CredentialStatus.active.value == "active"


def test_pipeline_run_has_task_id():
    assert "task_id" in PipelineRun.__table__.columns
    assert "project_id" not in PipelineRun.__table__.columns


def test_dws_credential_has_project_id():
    assert "project_id" in DwsCredential.__table__.columns


def test_task_has_credential_polymorphic_fields():
    cols = Task.__table__.columns
    assert "credential_id" in cols
    assert "credential_type" in cols
