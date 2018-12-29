#!/usr/bin/python
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import dagma.cli

if __name__ == '__main__':
    cli = dagma.cli.create_dagma_cli()
    cli(obj={})
