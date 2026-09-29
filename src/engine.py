"""道具使用系统。

修复点：
1. 使用时同时检查数量 + 等级/职业/场景/过期等条件
2. 独立冷却 + 公共冷却（cd_group 同类道具共享 CD）
3. 效果数据驱动：effect 字典 + 可注册的效果处理器，新增道具不改核心代码
4. 支持批量使用（count），批量需确认提示后才生效
5. 持续时间 = 基础时长 * 品质倍率（配置表驱动）
6. 可取消的 buff 支持主动取消，按剩余时间比例返还
7. 限时道具过期后可标记、可一键删除，过期道具不可使用
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time

# 品质 -> 持续时间倍率（纯配置，新增品质只改配置表）
QUALITY_DURATION_MULT = {
    "common": 1.0,
    "uncommon": 1.25,
    "rare": 1.5,
    "epic": 2.0,
    "legendary": 3.0,
}

DEFAULT_PLAYER_LEVEL = 1


@dataclass
class Item:
    iid: str
    name: str
    level_req: int
    cooldown: float
    duration: float
    effect: dict
    quality: str = "common"            # 品质，影响持续时间
    class_req: str | None = None       # 职业要求，None 表示不限
    scene_req: str | None = None       # 场景要求，None 表示不限
    cd_group: str | None = None        # 公共冷却组，同组道具共享 CD
    max_stack: int = 99                # 叠加上限
    cancellable: bool = False          # buff 是否可主动取消
    refund_ratio: float = 0.5          # 取消时按剩余时间的返还比例
    expire_at: float | None = None     # 过期时间戳，None 表示永不过期


@dataclass
class Buff:
    item_id: str
    effect: dict
    count: int
    start: float
    duration: float
    cancellable: bool
    refund_ratio: float

    @property
    def end(self) -> float:
        return self.start + self.duration

    def remaining(self, now: float) -> float:
        return max(0.0, self.end - now)


class ItemUseSystem:
    def __init__(self):
        self.items: dict[str, Item] = {}
        self.player_items: dict[str, dict[str, int]] = {}
        self.active_buffs: dict[str, dict[str, Buff]] = {}
        self.last_use: dict[str, dict[str, float]] = {}        # 独立冷却
        self.group_last_use: dict[str, dict[str, float]] = {}  # 公共冷却
        self.player_profiles: dict[str, dict] = {}             # 玩家等级/职业/场景
        # 效果处理器注册表：效果名 -> handler(player_id, item, value, count)
        self.effect_handlers: dict = {}

    # ---------- 效果（数据驱动） ----------

    def register_effect_handler(self, effect_key, handler):
        """注册新效果类型的处理器，新增道具效果无需改动核心代码。"""
        self.effect_handlers[effect_key] = handler

    def _default_effect_handler(self, player_id, item, value, count):
        return value * count

    def _apply_effects(self, player_id, item, count):
        applied = {}
        for key, value in item.effect.items():
            handler = self.effect_handlers.get(key, self._default_effect_handler)
            applied[key] = handler(player_id, item, value, count)
        return applied

    # ---------- 条件检查 ----------

    def check_conditions(self, player_id, item_id, *, player_level=None,
                         player_class=None, scene=None, now=None):
        """返回 (是否可用, 原因)，原因可用于 UI 提示。"""
        item = self.items.get(item_id)
        if item is None:
            return False, "道具不存在"
        if now is None:
            now = time.time()
        profile = self.player_profiles.get(player_id, {})
        level = player_level if player_level is not None \
            else profile.get("level", DEFAULT_PLAYER_LEVEL)
        pclass = player_class if player_class is not None else profile.get("class")
        pscene = scene if scene is not None else profile.get("scene")
        if level < item.level_req:
            return False, f"等级不足：需要 {item.level_req} 级"
        if item.class_req is not None and pclass != item.class_req:
            return False, f"职业不符：需要 {item.class_req}"
        if item.scene_req is not None and pscene != item.scene_req:
            return False, f"场景不符：需要在 {item.scene_req} 使用"
        if item.expire_at is not None and now >= item.expire_at:
            return False, "道具已过期"
        return True, "ok"

    # ---------- 冷却 ----------

    def _cooldown_remaining(self, player_id, item, now):
        """剩余冷却秒数：独立冷却与公共冷却取较大者，0 表示可用。"""
        remaining = 0.0
        last = self.last_use.get(player_id, {}).get(item.iid)
        if last is not None:
            remaining = max(remaining, item.cooldown - (now - last))
        if item.cd_group:
            group_last = self.group_last_use.get(player_id, {}).get(item.cd_group)
            if group_last is not None:
                remaining = max(remaining, item.cooldown - (now - group_last))
        return max(0.0, remaining)

    # ---------- 使用 ----------

    def confirm_message(self, item_id, count):
        """批量使用的确认提示文案，确认后带 confirmed=True 再调 use。"""
        item = self.items[item_id]
        return f"确认一次性使用 {count} 个【{item.name}】吗？"

    def use(self, player_id, item_id, count=1, *, confirmed=True, now=None,
            player_level=None, player_class=None, scene=None):
        if now is None:
            now = time.time()
        item = self.items.get(item_id)
        if item is None or count < 1:
            return False
        # 批量使用必须先确认
        if count > 1 and not confirmed:
            return False
        # 数量检查
        have = self.player_items.get(player_id, {}).get(item_id, 0)
        if have < count:
            return False
        # 条件检查：等级/职业/场景/过期
        ok, _reason = self.check_conditions(
            player_id, item_id, player_level=player_level,
            player_class=player_class, scene=scene, now=now)
        if not ok:
            return False
        # 冷却检查：独立 CD + 公共 CD
        if self._cooldown_remaining(player_id, item, now) > 0:
            return False
        # 扣除数量（批量一次性扣除）
        bag = self.player_items.setdefault(player_id, {})
        bag[item_id] = have - count
        # 记录冷却
        self.last_use.setdefault(player_id, {})[item_id] = now
        if item.cd_group:
            self.group_last_use.setdefault(player_id, {})[item.cd_group] = now
        # 应用数据驱动的效果
        applied = self._apply_effects(player_id, item, count)
        self.active_buffs.setdefault(player_id, {})[item_id] = Buff(
            item_id=item_id, effect=applied, count=count, start=now,
            duration=self.get_duration(item_id),
            cancellable=item.cancellable, refund_ratio=item.refund_ratio)
        return True

    # ---------- 持续时间 ----------

    def get_duration(self, item_id):
        """持续时间 = 基础时长 * 品质倍率，按配置表驱动。"""
        item = self.items[item_id]
        return item.duration * QUALITY_DURATION_MULT.get(item.quality, 1.0)

    # ---------- 取消 ----------

    def cancel_buff(self, player_id, item_id, now=None):
        """主动取消可取消的 buff，返还 剩余时间 * refund_ratio 秒，返回返还值。"""
        if now is None:
            now = time.time()
        buff = self.active_buffs.get(player_id, {}).get(item_id)
        if buff is None or not buff.cancellable:
            return False
        refund = buff.remaining(now) * buff.refund_ratio
        del self.active_buffs[player_id][item_id]
        return refund

    # ---------- 过期清理 ----------

    def list_expired(self, player_id, current_time):
        """标记（不删除）背包中已过期的限时道具，返回道具 id 列表。"""
        expired = []
        for iid, count in self.player_items.get(player_id, {}).items():
            if count <= 0:
                continue
            item = self.items.get(iid)
            if item is not None and item.expire_at is not None \
                    and current_time >= item.expire_at:
                expired.append(iid)
        return expired

    def cleanup_expired(self, player_id, current_time):
        """一键删除过期道具，顺带结束已到期的 buff，返回被清理的道具 id 列表。"""
        expired = self.list_expired(player_id, current_time)
        bag = self.player_items.get(player_id, {})
        for iid in expired:
            bag.pop(iid, None)
        buffs = self.active_buffs.get(player_id, {})
        for iid in [iid for iid, buff in buffs.items() if buff.end <= current_time]:
            del buffs[iid]
        return expired
