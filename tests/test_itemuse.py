"""道具使用系统七个修复点的回归测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.engine import ItemUseSystem, Item

T = 1000.0  # 固定时间基准，测试可复现


def make_system(**item_kw):
    ius = ItemUseSystem()
    kw = dict(iid="i1", name="potion", level_req=1, cooldown=10,
              duration=60, effect={"hp": 100})
    kw.update(item_kw)
    ius.items = {"i1": Item(**kw)}
    ius.player_items = {"p1": {"i1": 5}}
    return ius


class TestConditions:
    def test_level_checked(self):
        ius = make_system(level_req=50)
        assert ius.use("p1", "i1", now=T, player_level=1) is False
        assert ius.player_items["p1"]["i1"] == 5  # 未消耗
        assert ius.use("p1", "i1", now=T, player_level=60) is True

    def test_class_and_scene_checked(self):
        ius = make_system(class_req="warrior", scene_req="arena")
        assert ius.use("p1", "i1", now=T, player_class="mage", scene="arena") is False
        assert ius.use("p1", "i1", now=T, player_class="warrior", scene="town") is False
        assert ius.use("p1", "i1", now=T, player_class="warrior", scene="arena") is True


class TestCooldown:
    def test_individual_cooldown(self):
        ius = make_system(cooldown=10)
        assert ius.use("p1", "i1", now=T) is True
        assert ius.use("p1", "i1", now=T + 5) is False   # 独立 CD 未好
        assert ius.use("p1", "i1", now=T + 11) is True

    def test_shared_group_cooldown(self):
        ius = make_system(cooldown=10, cd_group="buff_potion")
        ius.items["i2"] = Item("i2", "potion2", 1, 10, 60,
                               {"atk": 10}, cd_group="buff_potion")
        ius.player_items["p1"]["i2"] = 5
        assert ius.use("p1", "i1", now=T) is True
        # 同组道具共享公共 CD，不能同时叠 5 种 buff 药
        assert ius.use("p1", "i2", now=T + 1) is False
        assert ius.use("p1", "i2", now=T + 11) is True


class TestDataDrivenEffect:
    def test_new_effect_without_core_change(self):
        ius = make_system(effect={"shield": 50})
        seen = {}
        ius.register_effect_handler(
            "shield", lambda pid, item, val, n: seen.setdefault(pid, val * n))
        assert ius.use("p1", "i1", 2, now=T) is True
        assert seen["p1"] == 100  # 新效果通过注册 handler 生效，未改核心代码
        assert ius.active_buffs["p1"]["i1"].effect["shield"] == 100


class TestBatch:
    def test_batch_use_consumes_all(self):
        ius = make_system()
        assert ius.use("p1", "i1", 3, now=T) is True
        assert ius.player_items["p1"]["i1"] == 2

    def test_batch_needs_confirmation(self):
        ius = make_system()
        assert "3" in ius.confirm_message("i1", 3)  # 有确认提示
        assert ius.use("p1", "i1", 3, confirmed=False, now=T) is False
        assert ius.player_items["p1"]["i1"] == 5  # 未确认不消耗

    def test_batch_insufficient_count(self):
        ius = make_system()
        assert ius.use("p1", "i1", 6, now=T) is False
        assert ius.player_items["p1"]["i1"] == 5


class TestDuration:
    def test_duration_scales_with_quality(self):
        ius = make_system(duration=60, quality="rare")
        assert ius.get_duration("i1") == 90  # 60 * 1.5
        ius.items["i1"].quality = "common"
        assert ius.get_duration("i1") == 60


class TestCancel:
    def test_cancel_refunds_partial_time(self):
        ius = make_system(duration=60, cancellable=True, refund_ratio=0.5)
        assert ius.use("p1", "i1", now=T) is True
        refund = ius.cancel_buff("p1", "i1", now=T + 20)
        assert refund == 20  # 剩余 40 秒 * 0.5
        assert "i1" not in ius.active_buffs["p1"]

    def test_non_cancellable_buff(self):
        ius = make_system(cancellable=False)
        assert ius.use("p1", "i1", now=T) is True
        assert ius.cancel_buff("p1", "i1", now=T + 1) is False
        assert "i1" in ius.active_buffs["p1"]


class TestExpiry:
    def test_expired_item_unusable_and_cleaned(self):
        ius = make_system(expire_at=T + 100)
        assert ius.use("p1", "i1", now=T + 200) is False  # 过期不可用
        assert ius.list_expired("p1", T + 200) == ["i1"]  # 可标记
        assert ius.cleanup_expired("p1", T + 200) == ["i1"]  # 一键删除
        assert ius.player_items["p1"] == {}

    def test_unexpired_item_kept(self):
        ius = make_system(expire_at=T + 100)
        assert ius.cleanup_expired("p1", T + 50) == []
        assert ius.player_items["p1"]["i1"] == 5
