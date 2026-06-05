from ledger import obsidian


def test_obsidian_package_exports_supported_adapter_surface():
    assert "main" not in obsidian.__all__
    assert callable(obsidian.load_config)
    assert callable(obsidian.run_import)
    assert callable(obsidian.sync_queue)
