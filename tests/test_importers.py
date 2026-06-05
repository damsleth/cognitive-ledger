from pathlib import Path

from ledger.importers import DoctorResult, ImportBackend, ImportOptions, ImportResult
from ledger.importers.backends import FolderBackend
from ledger.importers.cli import build_import_subparser


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


# ---------------------------------------------------------------------------
# FolderBackend unit tests
# ---------------------------------------------------------------------------

def test_folder_backend_doctor_ok(tmp_path: Path) -> None:
    result = FolderBackend(tmp_path).doctor()
    assert result.ok is True
    assert result.backend == "folder"
    assert not result.errors


def test_folder_backend_doctor_missing_root(tmp_path: Path) -> None:
    result = FolderBackend(tmp_path / "nonexistent").doctor()
    assert result.ok is False
    assert result.errors


def test_folder_backend_import_once_dry_run_counts_files(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A")
    (tmp_path / "b.md").write_text("# B")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("# C")
    (tmp_path / "not_md.txt").write_text("ignored")

    result = FolderBackend(tmp_path).import_once(ImportOptions(root=tmp_path, dry_run=True))
    assert result.backend == "folder"
    assert result.scanned == 3
    assert result.imported == 3
    assert result.skipped == 0
    assert result.ok


def test_folder_backend_import_once_missing_root_returns_error(tmp_path: Path) -> None:
    result = FolderBackend(tmp_path / "nonexistent").import_once(
        ImportOptions(root=tmp_path / "nonexistent", dry_run=True)
    )
    assert result.ok is False
    assert result.errors
    assert "not found" in result.errors[0]


def test_folder_backend_import_once_copies_to_dest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "note.md").write_text("# Hello")
    (src / "sub").mkdir()
    (src / "sub" / "nested.md").write_text("# Nested")

    dest = tmp_path / "inbox"
    result = FolderBackend(src).import_once(ImportOptions(root=src), dest=dest)

    assert result.ok
    assert result.scanned == 2
    assert result.imported == 2
    assert result.skipped == 0
    assert (dest / "note.md").exists()
    assert (dest / "sub" / "nested.md").exists()


def test_folder_backend_import_once_skips_existing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "note.md").write_text("# Hello")

    dest = tmp_path / "inbox"
    dest.mkdir()
    (dest / "note.md").write_text("# Already here")

    result = FolderBackend(src).import_once(ImportOptions(root=src), dest=dest)

    assert result.ok
    assert result.scanned == 1
    assert result.imported == 0
    assert result.skipped == 1
    assert (dest / "note.md").read_text() == "# Already here"


def test_folder_backend_satisfies_protocol(tmp_path: Path) -> None:
    assert isinstance(FolderBackend(tmp_path), ImportBackend)


# ---------------------------------------------------------------------------
# CLI parser structure tests (ledger import obsidian surface)
# ---------------------------------------------------------------------------

def _make_parser():
    import argparse
    p = argparse.ArgumentParser()
    build_import_subparser(p.add_subparsers(dest="command"))
    return p


def test_import_folder_import_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "folder", "import", "--root", "/tmp"])
    assert args.command == "import"
    assert args.import_backend == "folder"
    assert args.import_subcommand == "import"


def test_import_folder_doctor_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "folder", "doctor", "--root", "/tmp"])
    assert args.import_backend == "folder"
    assert args.import_subcommand == "doctor"


def test_import_obsidian_init_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "init", "--vault", "/tmp/vault"])
    assert args.import_backend == "obsidian"
    assert args.import_subcommand == "init"
    assert str(args.root) == str(Path("/tmp/vault").expanduser().resolve())


def test_import_obsidian_import_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "import", "--vault", "/tmp/vault"])
    assert args.import_subcommand == "import"


def test_import_obsidian_bootstrap_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "bootstrap", "--root", "/tmp/vault"])
    assert args.import_subcommand == "bootstrap"


def test_import_obsidian_watch_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "watch", "--vault", "/tmp/vault", "--debounce-seconds", "2.5"])
    assert args.import_subcommand == "watch"
    assert args.debounce_seconds == 2.5


def test_import_obsidian_daemon_start_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "daemon", "start", "--vault", "/tmp/vault"])
    assert args.import_subcommand == "daemon"
    assert args.daemon_subcommand == "start"


def test_import_obsidian_daemon_stop_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "daemon", "stop", "--vault", "/tmp/vault"])
    assert args.daemon_subcommand == "stop"


def test_import_obsidian_daemon_status_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "daemon", "status", "--vault", "/tmp/vault"])
    assert args.daemon_subcommand == "status"


def test_import_obsidian_doctor_subcommand_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "doctor", "--vault", "/tmp/vault"])
    assert args.import_subcommand == "doctor"


def test_import_obsidian_queue_sync_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "queue", "sync", "--vault", "/tmp/vault"])
    assert args.import_subcommand == "queue"
    assert args.queue_subcommand == "sync"


def test_import_obsidian_related_path_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "related", "--path", "/tmp/note.md"])
    assert args.import_subcommand == "related"
    assert args.note_path == "/tmp/note.md"


def test_import_obsidian_related_query_recognized() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "related", "--query", "search text"])
    assert args.query_text == "search text"


def test_import_obsidian_vault_and_root_are_mutually_exclusive() -> None:
    import pytest
    p = _make_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["import", "obsidian", "import", "--vault", "/a", "--root", "/b"])


def test_import_obsidian_import_with_limits() -> None:
    p = _make_parser()
    args = p.parse_args([
        "import", "obsidian", "import", "--vault", "/tmp/vault",
        "--max-files", "10", "--max-notes", "5",
    ])
    assert args.max_files == 10
    assert args.max_notes == 5


def test_import_obsidian_no_subcommand_returns_none() -> None:
    p = _make_parser()
    args = p.parse_args(["import", "obsidian", "--vault", "/tmp/vault"])
    assert args.import_backend == "obsidian"
    assert getattr(args, "import_subcommand", None) is None
