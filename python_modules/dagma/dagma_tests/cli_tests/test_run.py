from __future__ import print_function

import pytest

from dagster import DagsterInvariantViolationError, PipelineDefinition
from dagster.utils import script_relative_path

from dagma.cli.run import _do_run_command

NULL_PIPELINE = PipelineDefinition([])


def test_run_bad_requirements_path():
    with pytest.raises(DagsterInvariantViolationError, match='Could not find a requirements file'):
        _do_run_command(
            NULL_PIPELINE, [], [], 'gargjkhkh', script_relative_path('dagma_test_config.yml'), print
        )


def test_run_bad_dagma_config_path():
    with pytest.raises(DagsterInvariantViolationError, match='Could not find a dagma config file'):
        _do_run_command(
            NULL_PIPELINE,
            [],
            [],
            script_relative_path('dagma_test_requirements.txt'),
            'gargjkhkh',
            print,
        )


def test_run_null_pipeline():
    # with pytest.raises(DagsterInvariantViolationError):
    _do_run_command(
        NULL_PIPELINE,
        [],
        [],
        script_relative_path('dagma_test_requirements.txt'),
        script_relative_path('dagma_test_config.yml'),
        print,
    )

