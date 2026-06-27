"""模型类可 import 且列映射正确（不连数据库）。"""
from docupipe_manager.models.project import Project, ProjectStatus
from docupipe_manager.models.project_member import ProjectMember
from docupipe_manager.models.task import Task, TaskStatus, CredentialType
from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus
from docupipe_manager.models.pipeline_run import PipelineRun
from docupipe_manager.models.project_env_var import ProjectEnvVar


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


def test_project_env_var_mapping():
    assert ProjectEnvVar.__tablename__ == "project_env_vars"
    cols = ProjectEnvVar.__table__.columns
    assert "id" in cols
    assert "project_id" in cols
    assert "key" in cols
    assert "value" in cols
    assert "is_secret" in cols
    assert "description" in cols
    assert "created_by" in cols
    assert "created_at" in cols
    assert "updated_at" in cols
    assert cols["is_secret"].default is not None


def test_dws_credential_has_credential_type():
    cols = DwsCredential.__table__.columns
    assert "credential_type" in cols
    assert cols["credential_type"].default is not None


def test_job_model_has_required_columns():
    from docupipe_manager.models.job import Job
    cols = {c.name for c in Job.__table__.columns}
    assert {"id", "kind", "status", "pid", "exit_code", "started_at",
            "completed_at", "log_path", "command_text", "error_message",
            "trigger_type", "credential_id", "created_at"} <= cols


def test_job_kind_enum_values():
    from docupipe_manager.models.job import JobKind, JobStatus, JobTriggerType
    assert {k.value for k in JobKind} == {"docupipe_run", "credential_keepalive"}
    assert {k.value for k in JobStatus} == {"pending", "running", "succeeded", "failed", "cancelled"}
    assert {k.value for k in JobTriggerType} == {"manual", "scheduled"}


def test_job_credential_id_nullable():
    from docupipe_manager.models.job import Job
    assert Job.__table__.columns["credential_id"].nullable is True
