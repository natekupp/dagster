from collections import namedtuple

from dagster import (
    check,
    Dict,
    Field,
    List,
    PipelineConfigEvaluationError,
    PipelineDefinition,
    ReentrantInfo,
    String,
)
from dagster.core.execution import create_typed_environment, get_subset_pipeline
from dagster.core.types.evaluator import evaluate_config_value
from dagster.core.system_config.types import (
    define_maybe_optional_selector_field,
    SystemNamedDict,
    SystemNamedSelector,
)

from .config import (
    DEFAULT_PUT_OBJECT_ACL,
    DEFAULT_PUT_OBJECT_STORAGE_CLASS,
    VALID_AWS_REGIONS,
    VALID_S3_ACLS,
    VALID_STORAGE_CLASSES,
)


def _format_str_options(options):
    return '|'.join(['`\'{option}\'`'.format(option=option) for option in options])


DagmaAirflowEngineConfig = SystemNamedDict(name='DagmaAirflowEngineConfig', fields={})

DagmaLambdaEngineConfig = SystemNamedDict(
    name='DagmaLambdaEngineConfig',
    fields={
        'aws_region': Field(
            String,  # FIXME this should be an enum
            description='The AWS region in which to launch lambda functions. Note that this '
            'region must be compatible with the values for `execution_s3_bucket` and '
            '`runtime_s3_bucket`. Must be one of {valid_aws_regions}.'.format(
                valid_aws_regions=_format_str_options(VALID_AWS_REGIONS)
            ),
        ),
        'execution_s3_bucket': Field(
            String,
            description='The S3 bucket in which to store lambda functions. Note that this bucket '
            'must be in the region specified by `aws_region`.',
        ),
        'runtime_s3_bucket': Field(
            String,
            description='The S3 bucket in which to store the dagma '
            'runtime. Note that this bucket must be in the region '
            'specified by `aws_region`.',
        ),
        'storage_config': Field(
            SystemNamedDict(
                name='DagmaLambdaEngineS3StorageConfig',
                fields={
                    'put_object_kwargs': SystemNamedDict(
                        name='DagmaLambdaEngineS3StoragePutObjectKwargs',
                        fields={
                            'ACL': Field(
                                String,
                                default_value=DEFAULT_PUT_OBJECT_ACL,
                                description='The ACL to apply when lambda '
                                'functions are uploaded to the execution '
                                'bucket. Must be one of {valid_put_object_acls}. Default is '
                                '\'{default_put_object_acl}\'.'.format(
                                    valid_put_object_acls=_format_str_options(VALID_S3_ACLS),
                                    default_put_object_acl=DEFAULT_PUT_OBJECT_ACL,
                                ),
                            ),  # FIXME this should be an enum
                            'StorageClass': Field(
                                String,
                                default_value=DEFAULT_PUT_OBJECT_STORAGE_CLASS,
                                description='The StorageClass for lambda '
                                'functions uploaded to the execution '
                                'bucket. Must be one of {valid_storage_classes}. Default is '
                                '\'{default_put_object_storage_class}\'.'.format(
                                    valid_storage_classes=_format_str_options(
                                        VALID_STORAGE_CLASSES
                                    ),
                                    default_put_object_storage_class=DEFAULT_PUT_OBJECT_STORAGE_CLASS,
                                ),  # FIXME this should be an enum
                            ),
                        },
                    )
                },
            )
        ),
    },
)

DagmaConfigType = SystemNamedDict(
    name='DagmaConfig',
    fields={
        'engine': define_maybe_optional_selector_field(
            SystemNamedSelector(
                name='DagmaEngineConfig',
                fields={
                    'lambda': Field(DagmaLambdaEngineConfig),
                    'airflow': Field(DagmaAirflowEngineConfig),
                },
            )
        ),
        'requirements': Field(
            List(String),
            is_optional=True,
            description='A list of pip-installable python requirements to be installed in the '
            'remote execution environment. Note that installing in --editable mode (-e) is not '
            'supported.',
        ),
        'includes': Field(
            List(String),
            is_optional=True,
            description='A list of local files to include in the remote execution environment. '
            'Paths should be relative to the location of the config file, which is treated as the '
            'root directory -- i.e., paths must point to the same directory as the config file or '
            'any of its children, but not to parent and peer directories.',
        ),
    },
)


class DagmaConfig(namedtuple('_DagmaConfig', 'engine requirements includes')):
    def __new__(cls, engine=None, requirements=None, includes=None):
        check.opt_inst_param()


def construct_dagma_config(config_value, additional_requirements, additional_includes):
    return DagmaConfig(
        engine=construct_engine_config(config_value['engine']),
        requirements=RequirementsConfig(config_value['requirements'] + additional_requirements),
        includes=construct_includes(config_value['includes'], additional_includes),
    )


def create_typed_dagma_environment(
    pipeline, dagma_config=None, additional_requirements=None, additional_includes=None
):
    check.inst_param(pipeline, 'pipeline', PipelineDefinition)
    check.opt_dict_param(dagma_config, 'dagma_config')
    check.opt_list_param(additional_requirements, 'additional_requirements')
    check.opt_list_param(additional_includes, 'additional_includes')

    result = evaluate_config_value(DagmaConfigType, dagma_config)

    if not result.success:
        raise PipelineConfigEvaluationError(pipeline, result.errors, dagma_config)

    return construct_dagma_config(result.value, additional_requirements, additional_includes)


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
