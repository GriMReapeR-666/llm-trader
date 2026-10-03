"""Tests for the profiles subcommand."""
import pytest
import json


class TestProfilesSubcommand:
    """Test the profiles CLI subcommand logic."""

    def test_list_profiles_via_module(self):
        """list_profiles() should return 4 profiles."""
        from core.harness.quant_models_plugins import list_profiles
        profiles = list_profiles()
        assert len(profiles) == 4
        assert "paper" in profiles
        assert "live" in profiles
        assert "research" in profiles
        assert "backtest" in profiles

    def test_profile_detail_shows_plugins(self):
        """load_profile() should return list of plugin entries."""
        from core.harness.quant_models_plugins import load_profile
        for name in ["paper", "live", "research", "backtest"]:
            plugins = load_profile(name)
            assert len(plugins) > 0
            assert all("class_path" in p for p in plugins)
            assert all("kwargs" in p for p in plugins)

    def test_profile_plugin_count(self):
        """Each profile should have a reasonable number of plugins."""
        from core.harness.quant_models_plugins import load_profile
        counts = {name: len(load_profile(name)) for name in ["paper", "live", "research", "backtest"]}
        assert counts["paper"] >= 15
        assert counts["live"] >= 15
        assert counts["research"] >= 5
        assert counts["backtest"] >= 5

    def test_live_has_more_plugins_than_research(self):
        """Live should have more plugins than research (adds broker, execution)."""
        from core.harness.quant_models_plugins import load_profile
        assert len(load_profile("live")) > len(load_profile("research"))

    def test_json_output_format(self):
        """JSON output should be parseable and contain expected fields."""
        from core.harness.quant_models_plugins import list_profiles, load_profile
        profiles = list_profiles()
        out = {}
        for name, desc in profiles.items():
            plugins = load_profile(name)
            out[name] = {"description": desc, "n_plugins": len(plugins)}
        # Should be JSON-serializable
        json_str = json.dumps(out, indent=2)
        parsed = json.loads(json_str)
        assert "paper" in parsed
        assert parsed["paper"]["n_plugins"] > 0

    def test_profile_detail_json_format(self):
        """Single profile JSON should include class_path and kwargs."""
        from core.harness.quant_models_plugins import load_profile
        plugins = load_profile("paper")
        out = {
            "name": "paper",
            "description": "test",
            "n_plugins": len(plugins),
            "plugins": plugins,
        }
        json_str = json.dumps(out, indent=2)
        parsed = json.loads(json_str)
        assert len(parsed["plugins"]) == len(plugins)
        assert "class_path" in parsed["plugins"][0]

    def test_mount_profile_produces_services(self):
        """Mounting a profile should produce services in the context."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        for name in ["paper", "research", "backtest"]:
            ctx = TradingContext(name=f"test_{name}")
            mount_profile(ctx, name)
            assert len(ctx._services) > 0
            assert len(ctx._plugins) > 0
            ctx.dispose()

    def test_all_profiles_include_quant_bundle(self):
        """Every profile should include RetailQuantBundlePlugin."""
        from core.harness.quant_models_plugins import load_profile
        for name in ["paper", "live", "research", "backtest"]:
            plugins = load_profile(name)
            classes = [p["class_path"] for p in plugins]
            assert any("RetailQuantBundlePlugin" in c for c in classes)

    def test_invalid_profile_raises(self):
        """Invalid profile name should raise ValueError."""
        from core.harness.quant_models_plugins import load_profile
        with pytest.raises(ValueError, match="Unknown profile"):
            load_profile("nonexistent")
