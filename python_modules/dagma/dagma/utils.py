import contextlib
import errno
import os
import shutil
import tempfile

from collections import namedtuple


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


@contextlib.contextmanager
def tempdirs(i=1):
    try:
        dirs = []
        for _ in range(i):
            dirs.append(tempfile.mkdtemp())
        if not dirs:
            yield None
        if len(dirs) == 1:
            yield dirs[0]
        yield tuple(dirs)
    finally:
        for dir_ in dirs:
            try:
                shutil.rmtree(dir_)
            except IOError as exc:
                continue


def get_function_name(context):
    return '{run_id}_function'.format(run_id=context.run_id)


def get_step_key(context, step_idx):
    return '{run_id}_step_{step_idx}.pickle'.format(run_id=context.run_id, step_idx=step_idx)


def get_input_key(context, step_idx):
    return '{run_id}_intermediate_results_{step_idx}.pickle'.format(
        run_id=context.run_id, step_idx=step_idx
    )


def get_deployment_package_key(context):
    return '{run_id}_deployment_package.zip'.format(run_id=context.run_id)


def get_resources_key(context):
    return '{run_id}_resources.pickle'.format(run_id=context.run_id)


LambdaInvocationPayload = namedtuple(
    'LambdaInvocationPayload',
    'run_id step_idx key s3_bucket s3_key_inputs s3_key_body ' 's3_key_resources s3_key_outputs',
)
