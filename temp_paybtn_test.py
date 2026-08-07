# -*- coding: utf-8 -*-
"""临时验证脚本: /激活互通 无参数时的购买按钮 keyboard。"""
import asyncio

import libs.ifdianQuery as mod
from libs.ifdianQuery import (
    ActivateInterconnectCommand,
    BuildInterconnectPayPayload,
    GetPayUrl,
    INTERCONNECT_PAY_OPTIONS,
)

POSTED = []
REPLIES = []


class MockMessage:
    def __init__(self, content, group_openid="GRP_CHAT"):
        self.content = content
        self.group_openid = group_openid
        self.id = "MSG_ID_001"
        self.author = type("A", (), {"member_openid": "M1", "user_openid": "U1"})()

    async def reply(self, content=None, **kwargs):
        REPLIES.append(content)
        return True


class MockApi:
    async def post_group_message(self, **kwargs):
        POSTED.append(kwargs)
        return True


async def main():
    # 1. GetPayUrl 三种配置形态
    mod._config_manager._config = {"IfdianPayUrl": "https://afdian.com/a/shop"}
    assert GetPayUrl(1) == GetPayUrl(2) == GetPayUrl(3) == "https://afdian.com/a/shop"
    print("[PASS] 字符串配置: 三个按钮共用同一 URL")

    mod._config_manager._config = {"IfdianPayUrl": {"1": "https://afdian.com/x1", "2": "https://afdian.com/x2", "3": "https://afdian.com/x3"}}
    assert GetPayUrl(1) == "https://afdian.com/x1"
    assert GetPayUrl(2) == "https://afdian.com/x2"
    assert GetPayUrl(3) == "https://afdian.com/x3"
    print("[PASS] 对象配置: 按月映射 URL")

    mod._config_manager._config = {}
    assert GetPayUrl(1) == ""
    print("[PASS] 未配置: 返回空串")

    # 2. keyboard 结构: 一行三按钮, type=0 URL 跳转
    mod._config_manager._config = {"IfdianPayUrl": {"1": "https://afdian.com/x1", "2": "https://afdian.com/x2", "3": "https://afdian.com/x3"}}
    markdown, keyboard = BuildInterconnectPayPayload()
    assert "激活互通" in markdown["content"]
    rows = keyboard["content"]["rows"]
    assert len(rows) == 1, "应只有一行"
    buttons = rows[0]["buttons"]
    assert len(buttons) == 3, f"应有一行三个按钮, got {len(buttons)}"
    labels = [b["render_data"]["label"] for b in buttons]
    assert labels == ["一个月", "两个月", "三个月"], labels
    urls = [b["action"]["data"] for b in buttons]
    assert urls == ["https://afdian.com/x1", "https://afdian.com/x2", "https://afdian.com/x3"], urls
    for b in buttons:
        assert b["action"]["type"] == 0, "URL 按钮 type 应为 0"
        assert b["action"]["at_bot_show_channel_list"] is False
    print("[PASS] keyboard 结构: 一行三按钮(一个月/两个月/三个月), type=0 URL 跳转")

    # 3. 命令无参数 + 未配置 URL -> 提示配置
    REPLIES.clear(); POSTED.clear()
    mod._config_manager._config = {"IfdianPayUrl": {}}
    await ActivateInterconnectCommand(api=MockApi(), message=MockMessage("/激活互通"))
    assert REPLIES and "IfdianPayUrl" in REPLIES[-1], REPLIES
    assert not POSTED
    print("[PASS] 未配置 URL: 提示管理员填写 IfdianPayUrl")

    # 4. 命令无参数 + 已配置 URL -> 发送 keyboard
    REPLIES.clear(); POSTED.clear()
    mod._config_manager._config = {"IfdianPayUrl": {"1": "https://afdian.com/x1", "2": "https://afdian.com/x2", "3": "https://afdian.com/x3"}}
    await ActivateInterconnectCommand(api=MockApi(), message=MockMessage("/激活互通"))
    assert not REPLIES
    assert len(POSTED) == 1, POSTED
    post = POSTED[0]
    assert post["group_openid"] == "GRP_CHAT"
    assert post["msg_type"] == 2
    assert post["msg_id"] == "MSG_ID_001"
    kb = post["keyboard"]["content"]
    assert len(kb["rows"][0]["buttons"]) == 3
    print("[PASS] 已配置 URL: 发送 markdown + keyboard(一行三按钮)")

    # 5. 有参数仍走激活流程
    REPLIES.clear(); POSTED.clear()

    class MockHandler:
        pass

    orig_activate = mod.ActivateOrder

    async def fake_activate(order_no, group_openid):
        assert order_no == "ORDER123" and group_openid == "GRP_CHAT"
        return {"code": 200, "msg": "激活成功,已绑定群聊",
                "data": {"expire_time": "2030-01-01 00:00:00", "remaining_days": 30.0}}

    mod.ActivateOrder = fake_activate
    await ActivateInterconnectCommand(api=MockApi(), message=MockMessage("/激活互通 ORDER123"))
    assert REPLIES and "激活成功" in REPLIES[-1], REPLIES
    assert not POSTED, "有参数时不应发送 keyboard"
    mod.ActivateOrder = orig_activate
    print("[PASS] 有参数: 仍走订单激活流程")

    print("\nALL PAY-BUTTON TESTS PASSED")


asyncio.run(main())
