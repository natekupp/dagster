from dagster import check, PipelineDefinition, ReentrantInfo
from dagster.core.execution import create_typed_environment, get_subset_pipeline


def execute_pipeline(
    pipeline,
    environment,
    dagma_config,
    additional_requirements,
    additional_includes,
    throw_on_error=True,
    reentrant_info=None,
    solid_subset=None,
):
    '''Returns iterator that yields :py:class:`SolidExecutionResult` for each
    solid executed in the pipeline.

    This is analogous to dagster.execute_pipeline_iterator. Eventually we will probably want to
    more cleanly abstract the parts of the execution engines that are shared.

    Parameters:
      pipeline (PipelineDefinition): The pipeline to run
      environment (dict): The environment that parametrizes this pipeline run.
      dagma_config (dict): Dagma-specific config.
      additional_requirements (list[str]): A list of pip-installable requirements.
      additional_includes (list[str]): A list of file paths to files to include in the remote
        execution environment.
      throw_on_error (bool): If True, the function throws when an error is encoutered rather than
        returning the py:class:`SolidExecutionResult` in an error-state. Default: True.
      reentrant_info (ReentrantInfo): Optional reentrant info for pipeline execution, Default: None.
      solid_subset (list[str]): Optionally specify a subset of solids (a sub-DAG) to execute, by
        solid name. Default: None
    '''

    check.inst_param(pipeline, 'pipeline', PipelineDefinition)
    check.opt_dict_param(environment, 'environment')
    check.bool_param(throw_on_error, 'throw_on_error')
    check.opt_inst_param(reentrant_info, 'reentrant_info', ReentrantInfo)
    check.opt_list_param(solid_subset, 'solid_subset', of_type=str)

    pipeline_to_execute = get_subset_pipeline(pipeline, solid_subset)
    typed_environment = create_typed_environment(pipeline_to_execute, environment)

    dagma_environment = create_typed_dagma_environment(
        pipeline_to_execute, dagma_config, additional_requirements, additional_includes
    )
    raise Exception()
    # Check that the pipeline has a dagma resource available

    # Munge the dagma includes into the resource
    # Munge the dagma requirements into the resource
    return []
