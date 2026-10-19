import sys
import pytest

def test_kill_switch_latch_singleton_identity():
    import data.realtime_news_ingest as rni
    import intelligence.graph_nodes as gn
    import data.kill_switch_latch as ksl

    latch_news = rni.get_kill_switch_latch()
    latch_graph = gn.get_kill_switch_latch()
    latch_direct = ksl.get_kill_switch_latch()

    assert latch_news is latch_graph, "News ingest and graph nodes must share the same KillSwitchLatch instance"
    assert latch_news is latch_direct, "News ingest and data.kill_switch_latch must share the same instance"

def test_no_duplicate_kill_switch_latch_in_sys_modules():
    import data.realtime_news_ingest
    import intelligence.graph_nodes
    
    # Check that kill_switch_latch is not loaded under two distinct module objects
    modules = [m for name, m in sys.modules.items() if name in ("kill_switch_latch", "data.kill_switch_latch") and m is not None]
    if len(modules) > 1:
        assert modules[0] is modules[1], "kill_switch_latch is loaded twice as distinct module objects"
