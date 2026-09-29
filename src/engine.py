"""道具使用 - 带7个bug"""
from dataclasses import dataclass, field
import time

@dataclass
class Item:
    iid: str
    name: str
    level_req: int
    cooldown: float
    duration: float
    effect: dict

class ItemUseSystem:
    def __init__(self):
        self.items: dict[str, Item] = {}
        self.player_items = {}
        self.active_buffs = {}
        self.last_use = {}

    def use(self, player_id, item_id, count=1):
        item = self.items[item_id]
        # bug1: 不检查等级
        # bug2: 无公共冷却
        if self.player_items.get(player_id, {}).get(item_id, 0) >= count:
            self.player_items[player_id][item_id] -= count
            # bug4: 只消耗1个
            self.active_buffs[player_id] = self.active_buffs.get(player_id, {})
            self.active_buffs[player_id][item_id] = item.effect
            return True
        return False

    def get_duration(self, item_id):
        # bug5: 固定
        return 60

    def cancel_buff(self, player_id, item_id):
        # bug6: 不能取消
        return False

    def cleanup_expired(self, player_id, current_time):
        # bug7: 不自动清理
        pass
