import pytest

from dagster import (
    DependencyDefinition,
    InputDefinition,
    Int,
    OutputDefinition,
    PipelineDefinition,
    dagster_type,
    execute_pipeline,
    solid,
)

from dagster.core.definitions import solids_in_topological_order

from dagster.core.definitions.dependency import (
    FanoutDependencyDefinition,
    FaninDependencyDefinition,
)

from dagster.core.execution_plan.create import create_stack_tracker, Sequence, StepTag


def test_sequence_pipeline():
    events = []

    def _produce_things():
        events.append('enqueue-1')
        yield 1
        events.append('enqueue-2')
        yield 2

    @solid(outputs=[OutputDefinition(Sequence)])
    def produce_sequence(_info):
        return Sequence(_produce_things)

    @solid(inputs=[InputDefinition('seq', Sequence)])
    def consume_sequence(_info, seq):
        assert isinstance(seq, Sequence)
        for val in seq.items():
            events.append('dequeue-{}'.format(val))

    pipeline_def = PipelineDefinition(
        name='sequence_pipeline',
        solids=[produce_sequence, consume_sequence],
        dependencies={'consume_sequence': {'seq': DependencyDefinition('produce_sequence')}},
    )

    result = execute_pipeline(pipeline_def)
    assert result.success

    assert events == ['enqueue-1', 'dequeue-1', 'enqueue-2', 'dequeue-2']


def define_basic_fanin_fanout_pipeline():
    def _produce_things():
        yield 1
        yield 2

    @solid(outputs=[OutputDefinition(Sequence)])
    def produce_sequence(_info):
        return Sequence(_produce_things)

    @solid(inputs=[InputDefinition('num', Int)])
    def add_one(_info, num):
        return num + 1

    @solid(inputs=[InputDefinition('seq', Sequence)])
    def consume_sequence(_info, seq):
        for val in seq.items():
            print(val)

    return PipelineDefinition(
        name='sequence_pipeline',
        solids=[produce_sequence, add_one, consume_sequence],
        dependencies={
            'add_one': {'num': FanoutDependencyDefinition('produce_sequence')},
            'consume_sequence': {'seq': FaninDependencyDefinition('add_one')},
        },
    )


def test_basic_fanin_fanout_dep_structure():
    pipeline_def = define_basic_fanin_fanout_pipeline()

    add_one_solid = pipeline_def.solid_named('add_one')
    assert pipeline_def.dependency_structure.is_fanout_dep(add_one_solid.input_handle('num'))
    assert not pipeline_def.dependency_structure.is_fanin_dep(add_one_solid.input_handle('num'))

    consume_sequence_solid = pipeline_def.solid_named('consume_sequence')
    assert not pipeline_def.dependency_structure.is_fanout_dep(
        consume_sequence_solid.input_handle('seq')
    )
    assert pipeline_def.dependency_structure.is_fanin_dep(
        consume_sequence_solid.input_handle('seq')
    )


def test_stack_builder():
    pipeline_def = define_basic_fanin_fanout_pipeline()
    stack_entries = create_stack_tracker(
        pipeline_def, solids_in_topological_order(pipeline_def)
    ).stack_entries
    assert len(stack_entries) == 3
    assert len(stack_entries['produce_sequence'].plan_builder_stack) == 1
    assert len(stack_entries['add_one'].plan_builder_stack) == 2
    assert len(stack_entries['consume_sequence'].plan_builder_stack) == 1

    assert (
        stack_entries['produce_sequence'].plan_builder_stack[0]
        == stack_entries['add_one'].plan_builder_stack[0]
    )

    assert (
        stack_entries['produce_sequence'].plan_builder_stack[0]
        == stack_entries['consume_sequence'].plan_builder_stack[0]
    )


from dagster.core.execution import create_execution_plan, execute_plan, create_execution_plan


def define_fanout_only_pipeline():
    def _produce_things():
        yield 1
        yield 2

    @solid(outputs=[OutputDefinition(Sequence)])
    def produce_sequence(_info):
        return Sequence(_produce_things)

    @solid(inputs=[InputDefinition('num', Int)])
    def add_one(_info, num):
        return num + 1

    return PipelineDefinition(
        name='only_fanout_pipeline',
        solids=[produce_sequence, add_one],
        dependencies={'add_one': {'num': FanoutDependencyDefinition('produce_sequence')}},
    )


def test_only_fanout_create_execution_plan():
    fanout_pipeline = define_fanout_only_pipeline()

    plan = create_execution_plan(fanout_pipeline)
    assert plan

    assert len(plan.steps) == 2
    assert plan.steps[0].tag == StepTag.TRANSFORM
    assert plan.steps[1].tag == StepTag.SUBPLAN_EXECUTOR

    subplan = plan.steps[1].subplan
    assert len(subplan.steps) == 1
    assert subplan.steps[0].tag == StepTag.TRANSFORM


@pytest.mark.skip('not yet')
def test_only_fanout_execute_pipeline():
    fanout_pipeline = define_fanout_only_pipeline()
    plan = create_execution_plan(fanout_pipeline)
    results = execute_plan(fanout_pipeline, plan)
    out_list = list(results[1].success_data.value.items())
    assert out_list == [2, 3]
