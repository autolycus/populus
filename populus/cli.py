import os
import time
import signal

import pytest

import click

from watchdog.observers.polling import (
    PollingObserver,
)
from watchdog.events import FileSystemEventHandler

from eth_rpc_client import Client

from populus import utils
from populus.compilation import (
    get_contracts_dir,
    compile_and_write_contracts,
)

from populus.geth import (
    get_geth_data_dir,
    geth_wrapper,
    run_geth_node,
    ensure_account_exists,
    reset_chain,
)


class ContractChangedEventHandler(FileSystemEventHandler):
    """
    > http://pythonhosted.org/watchdog/api.html#watchdog.events.FileSystemEventHandler
    """
    def __init__(self, *args, **kwargs):
        self.project_dir = kwargs.pop('project_dir')
        self.contract_filters = kwargs.pop('contract_filters')

    def on_any_event(self, event):
        click.echo("============ Detected Change ==============")
        click.echo("> {0} => {1}".format(event.event_type, event.src_path))
        click.echo("> recompiling...")
        compile_and_write_contracts(self.project_dir, *self.contract_filters)
        click.echo("> watching...")


@click.group()
def main():
    pass


@main.command('compile')
@click.option(
    '--watch',
    '-w',
    is_flag=True,
    help="Watch contract source files and recompile on changes",
)
@click.argument('contracts', nargs=-1)
def compile_contracts(watch, contracts):
    """
    Compile project contracts, storing their output in `./build/contracts.json`

    Call bare to compile all contracts or specify contract names or file paths
    to restrict to only compiling those contracts.

    Pass in a file path and a contract name separated by a colon(":") to
    specify only named contracts in the specified file.
    """
    project_dir = os.getcwd()

    click.echo("============ Compiling ==============")
    click.echo("> Loading contracts from: {0}".format(get_contracts_dir(project_dir)))

    result = compile_and_write_contracts(project_dir, *contracts)
    contract_source_paths, compiled_sources, output_file_path = result

    click.echo("> Found {0} contract source files".format(len(contract_source_paths)))
    for path in contract_source_paths:
        click.echo("- {0}".format(os.path.basename(path)))
    click.echo("")
    click.echo("> Compiled {0} contracts".format(len(compiled_sources)))
    for contract_name in sorted(compiled_sources.keys()):
        click.echo("- {0}".format(contract_name))
    click.echo("")
    click.echo("> Outfile: {0}".format(output_file_path))

    if watch:
        # The path to watch
        watch_path = utils.get_contracts_dir(project_dir)

        click.echo("============ Watching ==============")

        event_handler = ContractChangedEventHandler(
            project_dir=project_dir,
            contract_filters=contracts,
        )
        observer = PollingObserver()
        observer.schedule(event_handler, watch_path, recursive=True)
        observer.start()
        try:
            while observer.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()


@main.command()
def deploy():
    """
    Deploy contracts.
    """
    contracts = utils.load_contracts(os.getcwd())
    client = Client('127.0.0.1', '8545')

    deployed_contracts = utils.deploy_contracts(client, contracts)

    name_padding = max(len(n) + 1 for n in deployed_contracts.keys())
    for name, info in deployed_contracts.items():
        click.echo("{name} @ {addr} via txn:{txn_hash}".format(
            name=name.ljust(name_padding),
            addr=(info.get('addr') or '<pending>').ljust(42),
            txn_hash=info['txn'].ljust(66),
        ))


@main.command()
def test():
    """
    Test contracts (wrapper around py.test)
    """
    pytest.main(os.path.join(os.getcwd(), 'tests'))


from populus.web import (
    get_flask_app,
    collect_static_assets,
    compile_js_contracts,
)


@main.command()
@click.option('--debug/--no-debug', default=True)
def runserver(debug):
    """
    Run the development server.
    """
    flask_app = get_flask_app(os.getcwd())
    flask_app.debug = debug
    flask_app.run()


@main.command()
def collect_assets():
    compile_js_contracts(os.getcwd())
    collect_static_assets(os.getcwd())


@main.group()
def chain():
    """
    Wrapper around `geth`.
    """
    pass


@chain.command('reset')
@click.argument('name', nargs=1, default="default")
@click.option('--confirm/--no-confirm', default=True)
def chain_reset(name, confirm):
    """
    Reset a test chain
    """
    data_dir = get_geth_data_dir(os.getcwd(), name)
    if confirm and not click.confirm("Are you sure you want to reset blockchain '{0}': {1}".format(name, data_dir)):
        raise click.Abort()
    reset_chain(data_dir)


@chain.command('run')
@click.argument('name', nargs=1, default="default")
@click.option('--mine/--no-mine', default=True)
def chain_run(name, mine):
    """
    Run a geth node.
    """
    data_dir = get_geth_data_dir(os.getcwd(), name)

    if not os.path.exists(data_dir):
        if name == 'default':
            utils.ensure_path_exists(data_dir)
        elif click.confirm("The chain '{0}' does not exist.  Would you like to create it?".format(name)):
            utils.ensure_path_exists(data_dir)
        else:
            raise click.UsageError("Unknown chain '{0}'".format(name))

    ensure_account_exists(data_dir)

    command, proc = run_geth_node(data_dir, mine=mine)

    click.echo("Running: '{0}'".format(' '.join(command)))

    try:
        while True:
            out_line = proc.get_stdout_nowait()
            if out_line:
                click.echo(out_line, nl=False)

            err_line = proc.get_stderr_nowait()
            if err_line:
                click.echo(err_line, nl=False)

            if err_line is None and out_line is None:
                time.sleep(0.2)
    except KeyboardInterrupt:
        try:
            proc.send_signal(signal.SIGINT)
            # Give the subprocess a SIGINT and give it a few seconds to
            # cleanup.
            utils.wait_for_popen(proc)
            while not proc.stdout_queue.empty() or not proc.stderr_queue.empty():
                out_line = proc.get_stdout_nowait()
                if out_line:
                    click.echo(out_line, nl=False)

                err_line = proc.get_stderr_nowait()
                if err_line:
                    click.echo(err_line, nl=False)
        except:
            # Try a harder termination.
            proc.terminate()
            utils.wait_for_popen(proc, 2)
    if proc.poll() is None:
        # Force it to kill if it hasn't exited already.
        proc.kill()
    if proc.returncode:
        raise click.ClickException("Error shutting down geth process.")
