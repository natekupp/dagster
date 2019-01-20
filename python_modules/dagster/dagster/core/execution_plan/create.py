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
    StepTag,
    PlanBuilder,
    StepOutputMap,
    SolidStackEntry,
    StackTracker,
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
    return PlanBuilder(steps=[], step_output_map=StepOutputMap())


def create_stack_tracker(pipeline):
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

    for solid in solids_in_topological_order(pipeline):
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


def create_execution_plan_core(context, pipeline, environment):
    execution_info = CreateExecutionPlanInfo(
        context, pipeline, environment, create_stack_tracker(pipeline)
    )

    for solid in solids_in_topological_order(execution_info.pipeline):

        plan_builder = execution_info.builder_for_solid(solid)

        step_inputs = create_step_inputs(execution_info, solid)

        solid_transform_step = create_transform_step(
            execution_info, solid, step_inputs, get_solid_user_config(execution_info, solid)
        )

        plan_builder.steps.append(solid_transform_step)

        for output_def in solid.definition.output_defs:
            subplan = create_value_subplan_for_output(
                execution_info, solid, solid_transform_step, output_def
            )
            plan_builder.steps.extend(subplan.steps)

            output_handle = solid.output_handle(output_def.name)
            plan_builder.step_output_map[output_handle] = subplan.terminal_step_output_handle

    return create_execution_plan_from_steps(execution_info.stack_tracker.root_builder.steps)


def create_execution_plan_from_steps(steps):
    check.list_param(steps, 'steps', of_type=ExecutionStep)

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
            deps[step.key].add(step_input.prev_output_handle.step.key)

    return ExecutionPlan(step_dict, deps)


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


def create_value_subplan_for_output(execution_info, solid, solid_transform_step, output_def):
    check.inst_param(execution_info, 'execution_info', CreateExecutionPlanInfo)
    check.inst_param(solid, 'solid', Solid)
    check.inst_param(solid_transform_step, 'solid_transform_step', ExecutionStep)
    check.inst_param(output_def, 'output_def', OutputDefinition)

    subplan = decorate_with_expectations(execution_info, solid, solid_transform_step, output_def)

    return decorate_with_output_materializations(execution_info, solid, output_def, subplan)


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
        if dependency_structure.is_fanin_dep(input_handle):
            check.not_implemented('fanin')
        elif dependency_structure.is_fanout_dep(input_handle):
            check.not_implemented('fanout')
        else:
            solid_output_handle = dependency_structure.get_dep(input_handle)
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


def add_steps(info, solid, steps):
    info.builder_for_solid(solid).steps.extend(steps)


def create_step_inputs(info, solid):
    check.inst_param(info, 'info', CreateExecutionPlanInfo)
    check.inst_param(solid, 'solid', Solid)

    step_inputs = []

    for input_def in solid.definition.input_defs:
        prev_step_output_handle = get_input_source_step_handle(info, solid, input_def)

        subplan = create_value_subplan_for_input(info, solid, prev_step_output_handle, input_def)

        add_steps(info, solid, subplan.steps)
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

    return create_execution_plan_from_steps(steps)


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
