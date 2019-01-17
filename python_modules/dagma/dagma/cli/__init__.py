import click

from .run import run_command


def create_dagma_cli():
    group = click.Group()
    group.add_command(run_command)
    return group


def main():
    cli = create_dagma_cli()
    cli(obj={})
