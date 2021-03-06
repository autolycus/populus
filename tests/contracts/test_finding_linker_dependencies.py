import os

from populus.utils import load_contracts
from populus.contracts import (
    package_contracts,
)
from populus.contracts.utils import (
    get_linker_dependencies,
)


PROJECTS_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'projects')
project_dir = os.path.join(PROJECTS_DIR, 'test-03')


def test_extracting_linker_dependencies():
    contracts = package_contracts(load_contracts(project_dir))

    linker_deps = get_linker_dependencies(contracts)
    actual = {
        "PiggyBank": set(("AccountingLib",)),
    }

    assert linker_deps == actual
