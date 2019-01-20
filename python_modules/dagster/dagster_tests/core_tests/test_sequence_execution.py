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

from dagster.core.definitions.dependency import (
    FanoutDependencyDefinition,
    FaninDependencyDefinition,
)


@dagster_type
class Sequence(object):
    def __init__(self, iterable):
        self._iterable = iterable

    def items(self):
        for item in self._iterable():
            yield item


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


# @pytest.mark.skip('for now')
def test_basic_fan_out():
    pipeline_def = define_basic_fanin_fanout_pipeline()
    result = execute_pipeline(pipeline_def)
    assert result.success
