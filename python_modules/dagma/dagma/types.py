from collections import namedtuple

LambdaInvocationPayload = namedtuple(
    'LambdaInvocationPayload', 'run_id step_idx key s3_bucket s3_key_inputs s3_key_body '
    's3_key_resources s3_key_outputs'
)
