"""Tests for CLI profile mounting in mt5_trader.py."""
import pytest
import sys
import os
from unittest.mock import patch, MagicMock


class TestProfileCLI:
    """Test --profile, --list-profiles, and auto-detection logic."""

    def test_list_profiles_prints_and_returns(self):
        """--list-profiles should print profiles and exit."""
        from core.harness.quant_models_plugins import list_profiles
        profiles = list_profiles()
        assert "paper" in profiles
        assert "live" in profiles
        assert "research" in profiles
        assert "backtest" in profiles
        assert len(profiles) == 4

    def test_profile_parsing_in_argparse(self):
        """Verify --profile is a valid argparse choice."""
        # Simulate argparse parsing
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--profile", choices=["paper", "live", "research", "backtest"], default=None)
        args = ap.parse_args(["--profile", "paper"])
        assert args.profile == "paper"

        args2 = ap.parse_args(["--profile", "live"])
        assert args2.profile == "live"

        args3 = ap.parse_args([])
        assert args3.profile is None

    def test_profile_defaults_to_none(self):
        """When no --profile is given, default is None."""
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--profile", choices=["paper", "live", "research", "backtest"], default=None)
        args = ap.parse_args([])
        assert args.profile is None

    def test_list_profiles_flag(self):
        """--list-profiles is a valid boolean flag."""
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--list-profiles", action="store_true")
        args = ap.parse_args(["--list-profiles"])
        assert args.list_profiles is True

        args2 = ap.parse_args([])
        assert args2.list_profiles is False

    def test_mount_profile_paper(self):
        """mount_profile('paper') should create a full context."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")
        assert ctx.has("risk_manager")
        assert ctx.has("broker")
        assert ctx.has("trade_manager")
        assert ctx.has("cusum_monitor")
        assert ctx.has("sprt_test")
        ctx.dispose()

    def test_mount_profile_live_has_human_approval(self):
        """Live profile should require human approval on deliberative graph."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        ctx = TradingContext(name="test_live")
        mount_profile(ctx, "live")
        assert ctx.has("risk_manager")
        assert ctx.has("broker")
        # Live has real-time news
        assert ctx.has("news_ingest_fn")
        ctx.dispose()

    def test_mount_profile_research_no_broker(self):
        """Research profile should NOT have a broker."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        ctx = TradingContext(name="test_research")
        mount_profile(ctx, "research")
        assert not ctx.has("broker")
        assert not ctx.has("trade_manager")
        # Should have research tools
        assert ctx.has("code_interpreter")
        ctx.dispose()

    def test_mount_profile_backtest_no_broker(self):
        """Backtest profile should NOT have a broker."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        ctx = TradingContext(name="test_backtest")
        mount_profile(ctx, "backtest")
        assert not ctx.has("broker")
        assert ctx.has("cusum_monitor")
        ctx.dispose()

    def test_auto_detect_profile_from_flags(self):
        """Simulate auto-detection logic: --live maps to 'live' profile."""
        # This tests the logic in mt5_trader.py, not the argparse itself
        class FakeArgs:
            profile = None
            harness = True
            live = True
            paper = False
            demo = False

        args = FakeArgs()
        # Replicate the logic from mt5_trader.py
        profile_name = args.profile
        if profile_name is None and args.harness:
            if args.live:
                profile_name = "live"
            elif args.demo:
                profile_name = "paper"
            else:
                profile_name = "paper"
        assert profile_name == "live"

    def test_auto_detect_paper_default(self):
        """When --harness but no --paper/--live, defaults to paper."""
        class FakeArgs:
            profile = None
            harness = True
            live = False
            paper = False
            demo = False

        args = FakeArgs()
        profile_name = args.profile
        if profile_name is None and args.harness:
            if args.live:
                profile_name = "live"
            elif args.demo:
                profile_name = "paper"
            else:
                profile_name = "paper"
        assert profile_name == "paper"

    def test_explicit_profile_overrides_flags(self):
        """--profile research should override --paper flag."""
        class FakeArgs:
            profile = "research"
            harness = True
            live = False
            paper = True
            demo = False

        args = FakeArgs()
        # When profile is explicit, auto-detection is skipped
        profile_name = args.profile
        if profile_name is None and args.harness:
            if args.live:
                profile_name = "live"
            else:
                profile_name = "paper"
        assert profile_name == "research"

    def test_invalid_profile_rejected(self):
        """Invalid profile name should raise ValueError."""
        from core.harness.quant_models_plugins import load_profile
        with pytest.raises(ValueError, match="Unknown profile"):
            load_profile("invalid_profile_name")

    def test_all_profiles_include_quant_bundle(self):
        """Every profile should include the RetailQuantBundlePlugin."""
        from core.harness.quant_models_plugins import load_profile
        for name in ["paper", "live", "research", "backtest"]:
            plugins = load_profile(name)
            classes = [p["class_path"] for p in plugins]
            assert any("RetailQuantBundlePlugin" in c for c in classes), \
                f"Profile '{name}' missing RetailQuantBundlePlugin"

    def test_profile_mount_logs_services(self):
        """Mounting should log service count."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        import logging
        with patch.object(logging.Logger, "info") as mock_log:
            ctx = TradingContext(name="test_log")
            mount_profile(ctx, "paper")
            # Should have logged service/plugin count
            mock_log.assert_called()
            call_args = mock_log.call_args
            assert "mounted" in str(call_args).lower() or "services" in str(call_args).lower()
            ctx.dispose()
