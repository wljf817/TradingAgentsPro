"""正式的 ID 生成端口：随机 UUID。见 manual-plans spec「方案 ID」。"""

from tradingagentspro.integrations.random_ids import RandomIdGenerator
from tradingagentspro.shared.ids import IdGenerator


def test_random_ids_are_distinct_version_4_uuids() -> None:
    """spec「随机的方案 ID」：连续 1000 个都是版本 4 的 UUID，没有重复。"""
    ids: IdGenerator = RandomIdGenerator()

    issued = [ids.new_id() for _ in range(1000)]

    assert all(i.version == 4 for i in issued)
    assert len(set(issued)) == 1000
