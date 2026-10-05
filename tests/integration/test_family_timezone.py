"""家庭时区可修改：配置与家庭列表同步，“今天”随之变化；无效时区拒绝（离线）。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ledger.adapters.dynamo import keys
from ledger.application import families, repo
from ledger.domain.errors import ValidationFailed

from .conftest import World, new_key


def _set_tz(w: World, tz: str) -> dict:
    cfg = families.get_config(w.ctx, w.admin, w.fid)
    return families.update_config(
        w.ctx, w.admin, w.fid, new_key(), expected_version=cfg["version"], timezone=tz
    )


def test_change_timezone_updates_config_family_and_today(world: World) -> None:
    w = world
    out = _set_tz(w, "Asia/Shanghai")
    assert out["timezone"] == "Asia/Shanghai"
    meta = w.ctx.store.get(keys.family(w.fid), keys.META)
    assert meta is not None and meta["timezone"] == "Asia/Shanghai"  # 家庭信息同步
    # UTC 2026-10-05 12:00：奥克兰已是 10-06 01:00，上海仍是 10-05 20:00
    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    assert repo.family_today(repo.family_config(w.ctx, w.fid), now) == date(2026, 10, 5)


def test_invalid_timezone_rejected(world: World) -> None:
    with pytest.raises(ValidationFailed):
        _set_tz(world, "Mars/Olympus_Mons")
    assert families.get_config(world.ctx, world.admin, world.fid)["timezone"] == "Pacific/Auckland"
