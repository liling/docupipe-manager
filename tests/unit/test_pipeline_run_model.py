from docupipe_manager.models.pipeline_run import PipelineRun


def test_pipeline_run_keeps_task_binding_and_job_ref():
    cols = {c.name for c in PipelineRun.__table__.columns}
    assert {"id", "job_id", "task_id", "pipeline_name", "mode"} == cols


def test_pipeline_run_job_id_not_nullable():
    col = PipelineRun.__table__.columns["job_id"]
    assert col.nullable is False
