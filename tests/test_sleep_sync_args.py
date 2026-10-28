import pytest

from ledger.maintenance import main


def test_accept_drift_without_apply_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["sync", "--accept-drift"])
    assert exc.value.code == 2
    assert "--accept-drift requires --apply" in capsys.readouterr().err
