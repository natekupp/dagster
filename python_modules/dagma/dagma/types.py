from abc import ABC

from collections import namedtuple

LambdaInvocationPayload = namedtuple(
    'LambdaInvocationPayload',
    'run_id step_idx key s3_bucket s3_key_inputs s3_key_body ' 's3_key_resources s3_key_outputs',
)


class DagmaEngine(ABC):
    def __init__(self, config):
        pass

    def deploy_runtime(self):
        pass

    def deploy_pipeline(self, pipeline):
        pass

    def execute_solid_async(self, run_id):
        pass

    def execute_solid_sync(self, run_id):
        pass
