from dagster import check

from dagster.core.definitions import (
    solids_in_topological_order,
    InputDefinition,
    OutputDefinition,
    Solid,
)

from dagster.core.errors import DagsterInvariantViolationError

from .expectations import create_expectations_value_subplan, decorate_with_expectations

from .input_thunk import create_input_thunk_execution_step

from .materialization_thunk import decorate_with_output_materializations

from .objects import (
    ExecutionPlan,
    CreateExecutionPlanInfo,
    ExecutionStep,
    ExecutionValueSubPlan,
    ExecutionPlanSubsetInfo,
    StepInput,
    StepOutputHandle,
    StepOutput,
    StepTag,
    PlanBuilder,
    StepResult,
    SolidStackEntry,
    StackTracker,
    StepSuccessData,
)

from .transform import create_transform_step

from .utility import create_value_thunk_step


def get_solid_user_config(execution_info, pipeline_solid):
    check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)
    check.inst_param(pipeline_solid, 'pipeline_solid', Solid)

    name = pipeline_solid.name
    solid_configs = execution_info.environment.solids
    return solid_configs[name].config if name in solid_configs else None


def empty_plan_builder():
    return PlanBuilder(steps=[])


def create_stack_tracker(pipeline, topological_solids):
    root_builder = empty_plan_builder()
    initial_plan_builder_stack = [root_builder]

    stack_entries = {}

    def _set_stack_entry(new_entry):
        solid_name = new_entry.solid.name
        if solid_name not in stack_entries:
            stack_entries[solid_name] = new_entry
            return

        if len(new_entry.plan_builder_stack) != len(stack_entries[solid_name].plan_builder_stack):
            raise DagsterInvariantViolationError('stack mismatch!')

        for builder_one, builder_two in zip(
            new_entry.plan_builder_stack, stack_entries[solid_name].plan_builder_stack
        ):
            if not builder_one is builder_two:
                raise DagsterInvariantViolationError('stack mismatch!')

    dep_structure = pipeline.dependency_structure

    for solid in topological_solids:
        if not solid.definition.input_defs:
            _set_stack_entry(SolidStackEntry(solid, initial_plan_builder_stack))
            continue

        for input_def in solid.definition.input_defs:
            input_handle = solid.input_handle(input_def.name)
            if not dep_structure.has_dep(input_handle):
                _set_stack_entry(SolidStackEntry(solid, initial_plan_builder_stack))
                continue

            prev_output_handle = dep_structure.get_dep(input_handle)
            prev_stack_entry = stack_entries[prev_output_handle.solid.name]

            if dep_structure.is_fanout_dep(input_handle):
                _set_stack_entry(
                    SolidStackEntry(
                        solid, prev_stack_entry.plan_builder_stack + [empty_plan_builder()]
                    )
                )
            elif dep_structure.is_fanin_dep(input_handle):
                _set_stack_entry(SolidStackEntry(solid, prev_stack_entry.plan_builder_stack[:-1]))
            else:
                _set_stack_entry(SolidStackEntry(solid, prev_stack_entry.plan_builder_stack))

    return StackTracker(root_builder, stack_entries)


def get_fanout_input(execution_info, solid):
    dep_structure = execution_info.pipeline.dependency_structure

    def _is_fanin_inp(input_def):
        inp_handle = solid.input_handle(input_def.name)
        return dep_structure.has_dep(inp_handle) and dep_structure.is_fanin_dep(inp_handle)

    fanout_inputs = [input_def for input_def in solid.input_defs if _is_fanin_inp(input_def)]

    if len(fanout_inputs) > 1:
        raise DagsterInvariantViolationError('Cannot have more than one fanout input per solid')

    return fanout_inputs[0] if fanout_inputs else None


def is_fanout_composite_solid(execution_info, solid):
    return bool(get_fanout_input(execution_info, solid))


def create_execution_plan_core(context, pipeline, environment):
    topological_solids = solids_in_topological_order(pipeline)

    if not topological_solids:
        return create_execution_plan_from_steps(empty_plan_builder())

    stack_tracker = create_stack_tracker(pipeline, topological_solids)
    builder_stack = []
    topological_solids_by_plan_id = {}
    solid_to_plan_id = {}
    for solid in topological_solids:
        stack_entry = stack_tracker.stack_entries[solid.name]
        current_builder = stack_entry.plan_builder_stack[-1]
        current_plan_id = current_builder.plan_id
        solid_to_plan_id[solid.name] = current_plan_id
        if current_plan_id not in topological_solids_by_plan_id:
            topological_solids_by_plan_id[current_plan_id] = [solid]
            builder_stack.append(current_builder)
        else:
            topological_solids_by_plan_id[current_plan_id].append(solid)

    execution_info = CreateExecutionPlanInfo(
        context,
        pipeline,
        environment,
        stack_tracker,
        solid_to_plan_id,
        topological_solids_by_plan_id,
    )

    plans = []
    for plan_builder in reversed(builder_stack):
        for solid in topological_solids_by_plan_id[plan_builder.plan_id]:
            step_inputs = create_step_inputs(execution_info, solid)

            solid_transform_step = create_transform_step(
                execution_info, solid, step_inputs, get_solid_user_config(execution_info, solid)
            )

            plan_builder.steps.append(solid_transform_step)

            for output_def in solid.definition.output_defs:
                subplan = create_value_subplan_for_output(
                    execution_info, plan_builder, solid, solid_transform_step, output_def
                )
                plan_builder.steps.extend(subplan.steps)

                solid_output_handle = solid.output_handle(output_def.name)
                plan_builder.step_output_map[
                    solid_output_handle
                ] = subplan.terminal_step_output_handle

        new_plan = create_execution_plan_from_steps(plan_builder)
        plans.append(new_plan)
        execution_info.plans_dict[plan_builder.plan_id] = new_plan

    return plans[-1]


def create_execution_plan_from_steps(plan_builder):
    check.inst_param(plan_builder, 'plan_builder', PlanBuilder)
    steps = plan_builder.steps

    step_dict = {step.key: step for step in steps}
    deps = {step.key: set() for step in steps}

    seen_keys = set()

    for step in steps:
        if step.key in seen_keys:
            keys = [s.key for s in steps]
            check.failed(
                'Duplicated key {key}. Full list: {key_list}.'.format(key=step.key, key_list=keys)
            )

        seen_keys.add(step.key)

        for step_input in step.step_inputs:
            # For now. Should probably not checkin
            if not step_input.prev_output_handle is SUBPLAN_BEGIN_SENTINEL:
                deps[step.key].add(step_input.prev_output_handle.step.key)

    return ExecutionPlan(plan_builder.plan_id, step_dict, deps)


def create_value_subplan_for_input(execution_info, solid, prev_step_output_handle, input_def):
    check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)
    check.inst_param(solid, 'solid', Solid)
    check.inst_param(prev_step_output_handle, 'prev_step_output_handle', StepOutputHandle)
    check.inst_param(input_def, 'input_def', InputDefinition)

    if execution_info.environment.expectations.evaluate and input_def.expectations:
        return create_expectations_value_subplan(
            solid, input_def, prev_step_output_handle, tag=StepTag.INPUT_EXPECTATION
        )
    else:
        return ExecutionValueSubPlan.empty(prev_step_output_handle)


SUBPLAN_EXECUTOR_SEQUENCE_INPUT = 'sequence_input'
SUBPLAN_EXECUTOR_SEQUENCE_OUTPUT = 'sequence_output'

from dagster.core.definitions import Result
from dagster.core.types.decorator import dagster_type


@dagster_type
class Sequence(object):
    def __init__(self, iterable):
        self._iterable = iterable

    def items(self):
        for item in self._iterable():
            yield item


from .simple_engine import execute_plan_core


def _create_output_sequence(context, step, input_sequence, subplan):
    def _produce_output_sequence():
        for item in input_sequence.items():
            step_result = StepResult.success_result(step, step.tag, StepSuccessData('kjdfkd', item))

            intermediate_results = {SUBPLAN_BEGIN_SENTINEL: step_result}
            for inner_result in execute_plan_core(context, subplan, intermediate_results):
                # will need to check what output this is?
                # what to do on error?
                yield inner_result.success_data.value

    return Sequence(_produce_output_sequence)


def _create_subplan_executor_compute(subplan):
    check.inst_param(subplan, 'subplan', ExecutionPlan)

    def _do_subplan_executor_compute(context, step, inputs):
        input_sequence = inputs[SUBPLAN_EXECUTOR_SEQUENCE_INPUT]

        yield Result(
            _create_output_sequence(context, step, input_sequence, subplan),
            SUBPLAN_EXECUTOR_SEQUENCE_OUTPUT,
        )

    return _do_subplan_executor_compute


def create_subplan_executor_step(
    execution_info, solid, output_def, value_subplan, execution_step_input_handle_start
):
    subplan_id = execution_info.solid_to_plan_id[execution_step_input_handle_start.solid.name]
    # One input. Sequence from output

    # check or hardcode this?
    sequence_type = output_def.runtime_type

    subplan = execution_info.plans_dict[subplan_id]

    return ExecutionStep(
        key='plan.{}.executor'.format(subplan_id),
        step_inputs=[
            StepInput(
                SUBPLAN_EXECUTOR_SEQUENCE_INPUT,
                sequence_type,
                value_subplan.terminal_step_output_handle,
            )
        ],
        step_outputs=[StepOutput(SUBPLAN_EXECUTOR_SEQUENCE_OUTPUT, sequence_type)],
        compute_fn=_create_subplan_executor_compute(subplan),
        tag=StepTag.SUBPLAN_EXECUTOR,
        solid=solid,
        subplan=subplan,
    )


def decorate_with_subplan_executors(execution_info, plan_builder, solid, output_def, value_subplan):
    dep_structure = execution_info.pipeline.dependency_structure
    output_handle = solid.output_handle(output_def.name)
    subplan_executors = []
    for input_handle in dep_structure.input_handles_depending_on_output(output_handle):
        if dep_structure.is_fanout_dep(input_handle):
            subplan_executor_step = create_subplan_executor_step(
                execution_info, solid, output_def, value_subplan, input_handle
            )
            subplan_executors.append(subplan_executor_step)

            plan_builder.plan_output_map[
                subplan_executor_step.subplan.plan_id
            ] = StepOutputHandle.create(subplan_executor_step, SUBPLAN_EXECUTOR_SEQUENCE_OUTPUT)

    return ExecutionValueSubPlan(
        value_subplan.steps + subplan_executors, value_subplan.terminal_step_output_handle
    )


def create_value_subplan_for_output(
    execution_info, plan_builder, solid, solid_transform_step, output_def
):
    check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)
    check.inst_param(solid, 'solid', Solid)
    check.inst_param(solid_transform_step, 'solid_transform_step', ExecutionStep)
    check.inst_param(output_def, 'output_def', OutputDefinition)

    value_subplan = decorate_with_expectations(
        execution_info, solid, solid_transform_step, output_def
    )

    value_subplan = decorate_with_output_materializations(
        execution_info, solid, output_def, value_subplan
    )

    return decorate_with_subplan_executors(
        execution_info, plan_builder, solid, output_def, value_subplan
    )


# class CompositeExecutionStep(ExecutionStep):
#     def __new__(cls, **kwargs):  # pylint: disable=W0221
#         return super(CompositeExecutionStep, cls).__new__(cls, **kwargs)


# def create_fanout_composite_step(execution_info, solid):
#     check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)


def get_input_source_step_handle(execution_info, solid, input_def):
    check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)
    check.inst_param(solid, 'solid', Solid)
    check.inst_param(input_def, 'input_def', InputDefinition)

    plan_builder = execution_info.builder_for_solid(solid)
    input_handle = solid.input_handle(input_def.name)
    solid_config = execution_info.environment.solids.get(solid.name)
    dependency_structure = execution_info.pipeline.dependency_structure

    if solid_config and input_def.name in solid_config.inputs:
        input_thunk_output_handle = create_input_thunk_execution_step(
            execution_info, solid, input_def, solid_config.inputs[input_def.name]
        )
        plan_builder.steps.append(input_thunk_output_handle.step)
        return input_thunk_output_handle
    elif dependency_structure.has_dep(input_handle):
        solid_output_handle = dependency_structure.get_dep(input_handle)
        if dependency_structure.is_fanin_dep(input_handle):
            # We need to get the step output handle of the subplan executor
            subplan_id = execution_info.solid_to_plan_id[solid_output_handle.solid.name]
            return plan_builder.plan_output_map[subplan_id]
        elif dependency_structure.is_fanout_dep(input_handle):
            # This is going to be the entry point into the subplan
            return SUBPLAN_BEGIN_SENTINEL
        else:
            return plan_builder.step_output_map[solid_output_handle]
    else:
        raise DagsterInvariantViolationError(
            (
                'In pipeline {pipeline_name} solid {solid_name}, input {input_name} '
                'must get a value either (a) from a dependency or (b) from the '
                'inputs section of its configuration.'
            ).format(
                pipeline_name=execution_info.pipeline.name,
                solid_name=solid.name,
                input_name=input_def.name,
            )
        )


SUBPLAN_BEGIN_SENTINEL = StepOutputHandle.sentinel()


def create_step_inputs(execution_info, solid):
    check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)
    check.inst_param(solid, 'solid', Solid)

    step_inputs = []

    plan_builder = execution_info.builder_for_solid(solid)

    for input_def in solid.definition.input_defs:
        prev_step_output_handle = get_input_source_step_handle(execution_info, solid, input_def)

        subplan = create_value_subplan_for_input(
            execution_info, solid, prev_step_output_handle, input_def
        )

        plan_builder.steps.extend(subplan.steps)
        step_inputs.append(
            StepInput(input_def.name, input_def.runtime_type, subplan.terminal_step_output_handle)
        )

    return step_inputs


def create_subplan(execution_plan, subset_info):
    check.inst_param(execution_plan, 'execution_plan', ExecutionPlan)
    check.inst_param(subset_info, 'subset_info', ExecutionPlanSubsetInfo)

    steps = []

    for step in execution_plan.steps:
        if step.key not in subset_info.subset:
            # Not included in subset. Skip.
            continue

        if step.key not in subset_info.inputs:
            steps.append(step)
        else:
            steps.extend(_create_new_steps_for_input(step, subset_info))

    return create_execution_plan_from_steps(PlanBuilder(steps))


def _create_new_steps_for_input(step, subset_info):
    new_steps = []
    new_step_inputs = []
    for step_input in step.step_inputs:
        if step_input.name in subset_info.inputs[step.key]:
            value_thunk_step_output_handle = create_value_thunk_step(
                step.solid,
                step_input.runtime_type,
                step.key + '.input.' + step_input.name + '.value',
                subset_info.inputs[step.key][step_input.name],
            )

            new_value_step = value_thunk_step_output_handle.step

            new_steps.append(new_value_step)

            new_step_inputs.append(
                StepInput(step_input.name, step_input.runtime_type, value_thunk_step_output_handle)
            )
        else:
            new_step_inputs.append(step_input)

    new_steps.append(step.with_new_inputs(new_step_inputs))
    return new_steps
