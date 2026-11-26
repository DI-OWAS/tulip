from os.path import join
from typing import Final

from waflib.extras.test_base import summary
from waflib.extras.symwaf2ic import get_toplevel_path
from waflib import Utils

TOPLEVEL: Final[str] = get_toplevel_path()
PYCODESTYLE_CONFIG: Final[str] = join(TOPLEVEL, "code-format", "pycodestyle")
PYLINT_CONFIG: Final[str] = join(TOPLEVEL, "code-format", "pylintrc")


def depends(dep):
    dep("code-format")


def options(opt):
    opt.load("python")
    opt.load("pytest")
    opt.load("pylint")
    opt.load("pycodestyle")


def configure(cfg):
    cfg.load("python")
    cfg.check_python_version((3, 8))
    cfg.load("pytest")
    cfg.load("pycodestyle")
    cfg.load("pylint")


def build(bld):
    bld(name=f"tulip_pylib",
        features="py",
        source=bld.path.ant_glob("src/py/tulip/**/*.py"),
        relative_trick=True,
        install_from="src/py",
        )

    bld(name=f"tulip_scripts",
        features="py",
        source=bld.path.ant_glob(f"src/py/tulip/scripts/**/*.py"),
        use="tulip_pylib",
        install_path="${PREFIX}/bin",
        install_from="src/py/tulip/scripts",
        chmod=Utils.O755,
        )

    bld(name="tulip_python_code_analysis",
        tests=bld.path.ant_glob("src/py/**/*.py"),
        features="pylint pycodestyle",
        pycodestyle_config=PYCODESTYLE_CONFIG,
        pylint_config=PYLINT_CONFIG,
        )

    bld(name="tulip_tests",
        tests=bld.path.ant_glob("tests/py/**/*.py"),
        features="pytest pylint pycodestyle",
        use="tulip_pylib",
        pycodestyle_config=PYCODESTYLE_CONFIG,
        pylint_config=PYLINT_CONFIG,
        )

    bld.add_post_fun(summary)
