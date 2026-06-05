from pathlib import Path

from ledger.importers import DoctorResult, ImportBackend, ImportOptions, ImportResult


class DummyBackend:
    name = "dummy"

    def doctor(self) -> DoctorResult:
        return DoctorResult(backend=self.name, ok=True, checks={"root": "ok"})

    def import_once(self, options: ImportOptions) -> ImportResult:
        return ImportResult(backend=self.name, scanned=1, imported=0, skipped=1)


def test_import_backend_protocol_and_results(tmp_path: Path) -> None:
    backend = DummyBackend()

    assert isinstance(backend, ImportBackend)
    assert backend.doctor().ok is True

    result = backend.import_once(ImportOptions(root=tmp_path, dry_run=True))

    assert result.ok is True
    assert result.backend == "dummy"
    assert result.scanned == 1
    assert result.skipped == 1


def test_importer_package_does_not_use_keyword_module_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert not (repo_root / "ledger" / "import").exists()
