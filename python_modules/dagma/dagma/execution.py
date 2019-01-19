import boto3
from requirements import parse as parse_requirements

from abc import ABC
from collections import namedtuple

from dagster import (
    check,
    ConfigType,
    DagsterEvaluateConfigValueError,
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
from dagster.utils import single_item

from .aws_lambda import Storage

from .config import (
    DEFAULT_PUT_OBJECT_ACL,
    DEFAULT_PUT_OBJECT_STORAGE_CLASS,
    VALID_AWS_REGIONS,
    VALID_S3_ACLS,
    VALID_STORAGE_CLASSES,
)
from .utils import format_str_options


DagmaAirflowEngineConfigType = SystemNamedDict(name='DagmaAirflowEngineConfig', fields={})

DagmaLambdaEngineConfigType = SystemNamedDict(
    name='DagmaLambdaEngineConfig',
    fields={
        'aws_access_key_id': Field(
            String,
            is_optional=True,
            description='Optionally specify the aws_access_key_id, overriding the usual boto3 '
            'credential chain. If you set this parameter, you must also set aws_secret_access_key, '
            'and you may not set aws_session_token.',
        ),
        'aws_secret_access_key': Field(
            String,
            is_optional=True,
            description='Optionally specify the aws_secret_access_key, overriding the usual boto3 '
            'credential chain. If you set this parameter, you must also set aws_access_key_id, '
            'and you may not set aws_session_token.',
        ),
        'aws_region': Field(
            String,  # FIXME this should be an enum
            description='The AWS region in which to launch lambda functions. Note that this '
            'region must be compatible with the values for `execution_s3_bucket` and '
            '`runtime_s3_bucket`. Must be one of {valid_aws_regions}.'.format(
                valid_aws_regions=format_str_options(VALID_AWS_REGIONS)
            ),
        ),
        'aws_session_token': Field(
            String,
            is_optional=True,
            description='Optionally specify an AWS session token, overriding the usual boto3 '
            'credentiual chain. If you set this parameter, you must not also set '
            'aws_access_key_id or aws_secret_access_key.',
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
                    'put_object_kwargs': Field(
                        SystemNamedDict(
                            name='DagmaLambdaEngineS3StoragePutObjectKwargs',
                            fields={
                                'ACL': Field(
                                    String,
                                    is_optional=True,
                                    default_value=DEFAULT_PUT_OBJECT_ACL,
                                    description='The ACL to apply when lambda '
                                    'functions are uploaded to the execution '
                                    'bucket. Must be one of {valid_put_object_acls}. Default is '
                                    '\'{default_put_object_acl}\'.'.format(
                                        valid_put_object_acls=format_str_options(VALID_S3_ACLS),
                                        default_put_object_acl=DEFAULT_PUT_OBJECT_ACL,
                                    ),
                                ),  # FIXME this should be an enum
                                'StorageClass': Field(
                                    String,
                                    is_optional=True,
                                    default_value=DEFAULT_PUT_OBJECT_STORAGE_CLASS,
                                    description='The StorageClass for lambda '
                                    'functions uploaded to the execution '
                                    'bucket. Must be one of {valid_storage_classes}. Default is '
                                    '\'{default_put_object_storage_class}\'.'.format(
                                        valid_storage_classes=format_str_options(
                                            VALID_STORAGE_CLASSES
                                        ),
                                        default_put_object_storage_class=DEFAULT_PUT_OBJECT_STORAGE_CLASS,
                                    ),  # FIXME this should be an enum
                                ),
                            },
                        ),
                        is_optional=True,
                        default_value={
                            'ACL': DEFAULT_PUT_OBJECT_ACL,
                            'StorageClass': DEFAULT_PUT_OBJECT_STORAGE_CLASS,
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
                    'lambda': Field(DagmaLambdaEngineConfigType),
                    'airflow': Field(DagmaAirflowEngineConfigType),
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
).inst()


class DagmaEngineConfig(ABC):
    '''Abstract base class for dagma engine configs.'''

    pass


class AirflowEngineConfig(namedtuple('_AirflowEngineConfig', ''), DagmaEngineConfig):
    pass


class LambdaEngineConfig(
    namedtuple('_LambdaEngineConfig', 'sessionmaker runtime_s3_bucket execution_s3_bucket storage'),
    DagmaEngineConfig,
):
    pass


def construct_engine_config(engine_config_value):
    engine, field = single_item(engine_config_value)
    if engine == 'airflow':
        return AirflowEngineConfig(**field)
    elif engine == 'lambda':
        aws_access_key_id = field.get('aws_access_key_id')
        aws_secret_access_key = field.get('aws_secret_access_key')
        aws_session_token = field.get('aws_session_token')
        aws_region_name = field['aws_region']
        runtime_s3_bucket = field['runtime_s3_bucket']
        execution_s3_bucket = field['execution_s3_bucket']

        if aws_access_key_id or aws_secret_access_key:
            if not (aws_access_key_id and aws_secret_access_key):
                raise DagsterEvaluateConfigValueError(
                    'Found a value for {found_key} but not {missing_key}. You must set both or '
                    'neither (and use the default boto3 credential chain instead).'.format(
                        found_key=(
                            'aws_access_key_id' if aws_access_key_id else 'aws_secret_access_key'
                        ),
                        missing_key=(
                            'aws_access_key_id'
                            if not aws_access_key_id
                            else 'aws_secret_access_key'
                        ),
                    )
                )
            if aws_session_token:
                raise DagsterEvaluateConfigValueError(
                    'You may not set aws_access_key_id or aws_secret_access_key when using an AWS '
                    'session token'
                )

        sessionmaker = lambda: boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=aws_region_name,
        )
        storage = Storage(sessionmaker, execution_s3_bucket, **field['storage_config'])
        return LambdaEngineConfig(
            sessionmaker=sessionmaker,
            runtime_s3_bucket=runtime_s3_bucket,
            execution_s3_bucket=execution_s3_bucket,
            storage=storage,
        )
    else:
        raise check.failed(
            'Shouldn\'t be here: Didn\'t recognize engine type \'{engine}\'. Supported engines '
            'are \'airflow\' and \'lambda\'.'.format(engine=engine)
        )


class DagmaConfig(namedtuple('_DagmaConfig', 'engine requirements includes')):
    def __new__(cls, engine=None, requirements=None, includes=None):
        check.opt_inst_param(engine, 'engine', DagmaEngineConfig)
        check.opt_list_param(requirements, 'requirements')
        check.opt_list_param(includes, 'includes')

        return super(DagmaConfig, cls).__new__(cls, engine, requirements, includes)


class DagmaRequirementsConfig(list):
    def __new__(cls, items):
        items = check.opt_list_param(items, 'items', of_type=str)

        parse_requirements('\n'.join(items))
        return super(DagmaRequirementsConfig, cls).__new__(cls, items)


def construct_dagma_config(config_value, additional_requirements, additional_includes):
    additional_requirements = check.opt_list_param(
        additional_requirements, 'additional_requirements', of_type=str
    )
    additional_includes = check.opt_list_param(
        additional_includes, 'additional_includes', of_type=str
    )

    requirements = config_value.get('requirements', [])
    includes = config_value.get('includes', [])

    return DagmaConfig(
        engine=construct_engine_config(config_value['engine']),
        requirements=DagmaRequirementsConfig(requirements + additional_requirements),
        includes=construct_includes(includes, additional_includes),
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
