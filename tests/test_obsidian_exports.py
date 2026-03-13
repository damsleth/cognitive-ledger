from ledger import obsidian


def test_obsidian_package_exports_supported_adapter_surface():
    assert "main" in obsidian.__all__
    assert "load_config" in obsidian.__all__
    assert "run_import" in obsidian.__all__
    assert "sync_queue" in obsidian.__all__

    assert callable(obsidian.main)
    assert callable(obsidian.load_config)
    assert callable(obsidian.run_import)
    assert callable(obsidian.sync_queue)
