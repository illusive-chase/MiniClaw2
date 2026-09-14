from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist
from pathlib import Path


def check_migrations() -> None:
    from miniclaw2.migrations.catalog import check_manifest

    check_manifest()


class CheckedBuild(build_py):
    def run(self) -> None:
        from miniclaw2.migrations.catalog import read_manifest

        check_migrations()
        super().run()
        expected = {entry["module"] + ".py" for entry in read_manifest()["steps"]}
        for path in (Path(self.build_lib) / "miniclaw2" / "migrations" / "steps").glob("v*.py"):
            if path.name not in expected:
                path.unlink()


class CheckedSource(sdist):
    def run(self) -> None:
        check_migrations()
        super().run()


setup(cmdclass={"build_py": CheckedBuild, "sdist": CheckedSource})
