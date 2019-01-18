"""Dagma config."""

from .version import __version__


DEFAULT_PUT_OBJECT_ACL = 'bucket-owner-full-control'
DEFAULT_PUT_OBJECT_STORAGE_CLASS = 'STANDARD'

ASSUME_ROLE_POLICY_DOCUMENT = """{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
"""

BUCKET_POLICY_DOCUMENT_TEMPLATE = """{{
    "Version": "2012-10-17",
    "Statement": [
        {{
            "Effect": "Allow",
            "Principal": {{
                "AWS": "{role_arn}"
            }},
            "Action": "*",
            "Resource": [
                "{bucket_arn}",
                "{bucket_arn}/*"
            ]
        }}
    ]
}}
"""

PYTHON_DEPENDENCIES = [
    'boto3',
    'cloudpickler',
    'git+ssh://git@github.com/dagster-io/dagster.git'
    '@{version}#egg=dagma&subdirectory=python_modules/dagma'.format(version=__version__),
]

VALID_AWS_REGIONS = [
    'us-east-1',
    'us-east-2',
    'us-west-1',
    'us-west-2',
    'ap-south-1',
    'ap-northeast-1',
    'ap-northeast-2',
    'ap-northeast-3',
    'ap-southeast-1',
    'ap-southeast-2',
    'ca-central-1',
    'cn-north-1',
    'cn-northwest-1',
    'eu-central-1',
    'eu-west-1',
    'eu-west-2',
    'eu-west-3',
    'eu-north-1',
    'sa-east-1',
]

VALID_S3_ACLS = [
    'private',
    'public-read',
    'public-read-write',
    'authenticated-read',
    'aws-exec-read',
    'bucket-owner-read',
    'bucket-owner-full-control',
]

VALID_STORAGE_CLASSES = [
    'STANDARD',
    'REDUCED_REDUNDANCY',
    'STANDARD_IA',
    'ONEZONE_IA',
    'INTELLIGENT_TIERING',
    'GLACIER',
]  # TODO Will GLACIER work?
