from ledger.importers.backends.obsidian import ObsidianBackend
from ledger.importers.backends.obsidian.config import load_config
from ledger.importers.backends.obsidian.importer import run_import
from ledger.importers.backends.obsidian.queue import sync_queue


def test_obsidian_backend_package_exports_backend_class():
    assert callable(ObsidianBackend)


def test_obsidian_backend_package_exports_core_functions():
    assert callable(load_config)
    assert callable(run_import)
    assert callable(sync_queue)


def test_legacy_ledger_obsidian_package_is_gone():
    try:
        import ledger.obsidian  # noqa: F401
    except ModuleNotFoundError:
        return
    raise AssertionError("ledger.obsidian should be fully removed — import the importers backend instead")
