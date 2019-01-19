import pytest

from dagster import PipelineConfigEvaluationError, PipelineDefinition

from dagma.execution import create_typed_dagma_environment

NULL_PIPELINE = PipelineDefinition([])


def test_create_typed_dagma_environment_bad_config():
    with pytest.raises(PipelineConfigEvaluationError):
        create_typed_dagma_environment(NULL_PIPELINE, {'engine': {'lambda': {}}})
    with pytest.raises(PipelineConfigEvaluationError):
        create_typed_dagma_environment(NULL_PIPELINE, {'engine': {'foobar': {}}})


def test_create_typed_dagma_environment():
    create_typed_dagma_environment(
        NULL_PIPELINE,
        {
            'engine': {
                'lambda': {
                    'aws_region': 'us-east-2',
                    'execution_s3_bucket': 'dagster-lambda-execution',
                    'runtime_s3_bucket': 'dagma-runtime-test',
                }
            }
        },
    )
