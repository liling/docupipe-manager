from docupipe_manager.models.pipeline_run import PipelineRun


def test_pipeline_run_has_command_text_column():
    cols = {c.name for c in PipelineRun.__table__.columns}
    assert "command_text" in cols


def test_pipeline_run_command_text_nullable():
    col = PipelineRun.__table__.columns["command_text"]
    assert col.nullable is True
