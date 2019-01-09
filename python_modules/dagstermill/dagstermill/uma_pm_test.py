import sys

sys.path.insert(0, '/Users/umaroy/Documents/uma_papermill')

import uma_papermill
from uma_papermill import execute
import json

r = {
    'pipeline_name': 'test_notebook_dag',
    'solid_def_name': 'mult_two_numbers',
    'marshal_dir': None,
    'inputs': {'a': 5, 'b': 6},
}

uma_papermill.execute_notebook(
    input_path='notebooks/new_mult_two_numbers.ipynb',
    output_path='notebooks/new_output.ipynb',
    parameters=dict(context=str(json.dumps(r))),
    injected_parameters_str="context = dm.convert_context(context)\n#Test newline",
)
