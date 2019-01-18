# TODO: figure out a versioning strategy so we can separate deploy from run

from __future__ import print_function

import glob
import logging
import os
import re
import textwrap
import yaml

import click
import requirements

from dagster import check, DagsterInvariantViolationError, PipelineDefinition
from dagster.cli.dynamic_loader import (
    load_pipeline_from_target_info,
    load_repository_from_target_info,
    load_repository_object_from_target_info,
    load_target_info_from_cli_args,
    pipeline_target_command,
    PipelineTargetInfo,
    repository_target_argument,
)
from dagster.cli.pipeline import create_pipeline_from_cli_args
from dagster.utils import load_yaml_from_path, load_yaml_from_glob_list

from ..execution import execute_pipeline

REPO_TARGET_WARNING = (
    'Can only use ONE of --repository-yaml/-y, --python-file/-f, --module-name/-m.'
)

LOGGING_DICT = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARN': logging.WARN,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}


def _get_additional_requirements(requirements_path):
    if requirements_path is None:
        return []

    with open(requirements_path, 'r') as fd:
        reqs = requirements.parse(fd)
        errors = []
        for req in reqs:
            if req.editable:
                errors.append(req.name)
        if errors:
            raise Exception(
                'Editable (-e/--editable) requirements are not supported: found {errors} in '
                'specified requirements file {requirements_file}'.format(
                    errors=', '.join(errors), requirements_file=requirements_path
                )
            )
        fd.seek(0)
        return filter(None, fd.read().split('\n'))


def _process_results_for_console(pipeline_iter):
    results = []

    for result in pipeline_iter:
        if not result.success:
            result.reraise_user_error()
        results.append(result)

    return results


def _execute_run_command(env, include, requirements_path, dagma_config_path, cli_args, print_fn):
    pipeline = create_pipeline_from_cli_args(cli_args)
    _do_run_command(pipeline, env, include, requirements_path, dagma_config_path, print_fn)


def _do_run_command(
    pipeline, env_file_list, include_file_list, requirements_path, dagma_config_path, printer
):
    check.inst_param(pipeline, 'pipeline', PipelineDefinition)
    check.callable_param(printer, 'printer')

    env_file_list = check.opt_list_param(env_file_list, 'env_file_list', of_type=str)

    include_file_list = check.opt_list_param(include_file_list, 'include_file_list', of_type=str)

    if requirements_path and not os.path.isfile(requirements_path):
        raise DagsterInvariantViolationError(
            'Could not find a requirements file at path {requirements_path}'.format(
                requirements_path=requirements_path
            )
        )

    if dagma_config_path and not os.path.isfile(dagma_config_path):
        raise DagsterInvariantViolationError(
            'Could not find a dagma config file at path {dagma_config_path}'.format(
                dagma_config_path=dagma_config_path
            )
        )

    env_config = load_yaml_from_glob_list(env_file_list) if env_file_list else {}

    additional_requirements = _get_additional_requirements(requirements_path)

    additional_includes = []
    for include_file_pattern in include_file_list:
        additional_includes.extend(glob.glob(include_file_pattern))

    dagma_config = load_yaml_from_path(dagma_config_path)

    pipeline_iter = execute_pipeline(
        pipeline, env_config, dagma_config, additional_requirements, additional_includes
    )

    _process_results_for_console(pipeline_iter)


@click.command(
    name='run',
    help='Run a pipeline on AWS lambda.\n\n{instructions}'.format(
        instructions=(
            'In order to use dagma from the command line, just like the other dagster command '
            'line utilities, you must point it at a repository or pipeline. Because dagma '
            'executes on AWS Lambda, not in your local Python environment, dagma requires some '
            'additional config to make sure dependencies are available in the remote '
            'environment.\n'
            '\n'
            '1. pip-installable packages required by the pipeline to run may be specified in '
            'config on the dagma resource, in a dagma.yml file, or a requirements.txt file. Note '
            'that the *union* of these requirements will be installed in the remote environment. '
            'Behavior in case of conflicting package versions is undefined.\n'
            '2. Local files outside of a pip-installable package required by the pipeline to run '
            'should be specified in a dagma.yml file or on the command line as includes '
            '(-i, --include). You may specify multiple relative file paths or globs in the root '
            'directory or its children -- parent directories and files in parent directories may '
            'not be included.\n'
            '\n'
            'It\'s strongly encouraged to make your dependencies pip installable, and to avoid '
            'using local files outside of packages except for prototyping. \n'
            '\n'
            'Note that installing in --editable mode (-e) is not supported.'
            '\n'
            'You may run your dagma pipelines as follows:'
            '\n'
            'From a directory containing a repository.yml and dagma.yml file:\n'
            '    $ dagma run <<pipeline_name>>\n'
            '\n'
            'Specifying a repository and dagma yaml:\n'
            '    $ dagma run <<pipeline_name>> -y path/to/repository.yml -d path/to/dagma.yml\n'
            '\n'
            'Specifying a python file and pipeline definition function:\n'
            '    $ dagma run -f path/to/file.py -n define_some_pipeline -d path/to/dagma.yml\n'
            '\n'
            'Specifying a module and pipeline definition function:\n'
            '    $ dagma run -m a_module.submodule -n define_some_pipeline -d path/to/dagma.yml'
        )
    ),
)
@pipeline_target_command
@click.option(
    '-e',
    '--env',
    type=click.STRING,
    multiple=True,
    help=(
        'Specify one or more environment files. These can also be file patterns/globs. '
        'If more than one environment file is captured then those files are merged. '
        'Files listed first take precendence. They will smash the values of subsequent '
        'files at the key-level granularity. If the file is a pattern then you must '
        'enclose it in double quotes'
        '\n\nExample: \n\n'
        '    dagster pipeline execute pandas_hello_world -e "pandas_hello_world/*.yml"'
        '\n\nYou can also explicitly specify multiple files:'
        '\n\nExample: \n\n'
        '    dagster pipeline execute pandas_hello_world -e pandas_hello_world/solids.yml '
        '-e pandas_hello_world/env.yml'
    ),
)
@click.option(
    '-d',
    '--dagma-config',
    type=click.STRING,
    required=True,
    help=(
        'Specify a dagma config yaml (required). Dagma yaml files shouldbe formatted as follows:\n'
        '\n'
        '    engine:\n'
        '      lambda: \n'
        '    requirements:\n'
        '      - numpy==1.15.4\n'
        '      - git+ssh://git@github.com/organization/project.git@tag#egg=project\n'
        '    includes:\n'
        '      - utils/**/*.py\n'
        '      - foo.py\n'
    ),
)
@click.option(
    '-r',
    '--requirements',
    type=click.STRING,
    help=(
        'Specify a requirements.txt file defining pip-installable packages required in the remote '
        'execution environment. Note that installing in --editable mode (-e) is not supported.\n'
        '\n'
        'Example: '
        'dagster pipeline execute hello_world -r requirements.txt'
    ),
)
@click.option(
    '-i',
    '--include',
    type=click.STRING,
    multiple=True,
    help=('Specify one or more files or file patterns/globs to include in the remote environment.'),
)
def run_command(env, include, requirements, dagma_config, **kwargs):  # pylint:disable=W0621
    check.invariant(isinstance(env, tuple))
    env = list(env)

    check.invariant(isinstance(include, tuple))
    include = list(include)

    check.opt_str_param(requirements, 'requirements')

    check.str_param(dagma_config, 'dagma_config')

    _execute_run_command(env, include, requirements, dagma_config, kwargs, click.echo)
