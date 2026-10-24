from harness_api.schema_panel_cache import SchemaPanelCache
from harness_api.schemas import PanelResult


def test_lru_copies_results_and_invalidation_blocks_inflight_fill():
    cache = SchemaPanelCache(max_entries=2)
    panel = PanelResult(
        operationId="schema.list_schemas",
        title="Schemas",
        available=True,
        collectedAt="original",
        rows=[["OWNER"]],
    )
    _, generation = cache.get(("a",))
    cache.put(("a",), panel, "exe_a", 60, generation)
    cache.put(("b",), panel, "exe_b", 60, generation)
    hit, _ = cache.get(("a",))
    hit[0].rows[0][0] = "MUTATED"
    assert cache.get(("a",))[0][0].rows == [["OWNER"]]
    cache.put(("c",), panel, "exe_c", 60, generation)
    assert cache.get(("b",))[0] is None
    cache.invalidate("a")
    cache.put(("a",), panel, "exe_late", 60, generation)
    assert cache.get(("a",))[0] is None
    assert cache.get(("c",))[0] is not None
