from dagster import (
    DependencyDefinition,
    InputDefinition,
    Int,
    OutputDefinition,
    PipelineDefinition,
    execute_pipeline,
    solid,
)

from dagster.core.definitions import solids_in_topological_order
from dagster.core.definitions.dependency import (
    FanoutDependencyDefinition,
    FaninDependencyDefinition,
)

from dagster.core.execution import execute_plan, create_execution_plan
from dagster.core.execution_plan.create import (
    SUBPLAN_EXECUTOR_SEQUENCE_OUTPUT,
    Sequence,
    StepTag,
    create_stack_tracker,
)


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


def define_basic_fanout_fanin_pipeline():
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
        output = []
        for val in seq.items():
            print(val)
            output.append(val)
        return output

    return PipelineDefinition(
        name='sequence_pipeline',
        solids=[produce_sequence, add_one, consume_sequence],
        dependencies={
            'add_one': {'num': FanoutDependencyDefinition('produce_sequence')},
            'consume_sequence': {'seq': FaninDependencyDefinition('add_one')},
        },
    )


def test_basic_fanin_fanout_dep_structure():
    pipeline_def = define_basic_fanout_fanin_pipeline()

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
    pipeline_def = define_basic_fanout_fanin_pipeline()
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


def test_only_fanout_execute_plan():
    fanout_pipeline = define_fanout_only_pipeline()
    plan = create_execution_plan(fanout_pipeline)
    results = execute_plan(fanout_pipeline, plan)
    out_list = list(results[1].success_data.value.items())
    assert out_list == [2, 3]


def test_basic_fanout_fanin_execution_plan():
    pipeline_def = define_basic_fanout_fanin_pipeline()
    plan = create_execution_plan(pipeline_def)
    assert len(plan.steps) == 3

    produce_sequence_transform_step = plan.steps[0]
    assert produce_sequence_transform_step.tag == StepTag.TRANSFORM
    assert not produce_sequence_transform_step.step_inputs
    assert len(produce_sequence_transform_step.step_outputs) == 1

    ps_t_output = produce_sequence_transform_step.step_outputs[0]
    assert ps_t_output.name == 'result'
    assert ps_t_output.runtime_type.name == 'Sequence'

    cst_step = plan.get_step_by_key('consume_sequence.transform')
    assert cst_step.key == 'consume_sequence.transform'
    assert len(cst_step.step_inputs) == 1
    cst_step_input = cst_step.step_inputs[0]
    assert cst_step_input.name == 'seq'
    assert cst_step_input.runtime_type.name == 'Sequence'

    assert cst_step_input.prev_output_handle.output_name == SUBPLAN_EXECUTOR_SEQUENCE_OUTPUT

    assert (
        plan.get_step_by_key(cst_step_input.prev_output_handle.step.key).tag
        == StepTag.SUBPLAN_EXECUTOR
    )

    assert plan.steps[1].tag == StepTag.SUBPLAN_EXECUTOR
    assert plan.steps[2].tag == StepTag.TRANSFORM


def test_basic_fanout_fanin_execute():
    pipeline_def = define_basic_fanout_fanin_pipeline()
    result = execute_pipeline(pipeline_def)
    assert result.success
    assert isinstance(result.result_for_solid('produce_sequence').transformed_value(), Sequence)
    assert result.result_for_solid('consume_sequence').transformed_value() == [2, 3]
