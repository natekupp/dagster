from dagster import PipelineDefinition

from dagma.execution import create_typed_dagma_environment

NULL_PIPELINE = PipelineDefinition([])


def test_create_typed_dagma_environment():

    create_typed_dagma_environment(NULL_PIPELINE, {'engine': {'lambda': {}}})
