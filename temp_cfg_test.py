# -*- coding: utf-8 -*-
"""临时验证脚本: 验证 ConfigManager 真实加载 IfdianServerUrl / IfdianPayUrl。"""
import asyncio

from libs.configManager import ConfigManager
from libs.ifdianQuery import (
    ActivateInterconnectCommand,
    BuildInterconnectPayPayload,
    GetIfdianServerUrl,
    GetPayUrl,
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
    # 1. ConfigManager 真实加载(不注入 _config)
    cm = ConfigManager()
    cfg = cm.Load()
    assert cfg["IfdianServerUrl"] == "http://127.0.0.1:8897", cfg.get("IfdianServerUrl")
    assert cfg["IfdianPayUrl"] == {"1": "https://afdian.com/x1", "2": "https://afdian.com/x2", "3": "https://afdian.com/x3"}, cfg.get("IfdianPayUrl")
    print("[PASS] ConfigManager.Load() 真实读取 IfdianServerUrl / IfdianPayUrl")

    # 2. ifdianQuery 通过真实配置读取
    assert GetIfdianServerUrl() == "http://127.0.0.1:8897"
    assert GetPayUrl(1) == "https://afdian.com/x1"
    assert GetPayUrl(2) == "https://afdian.com/x2"
    assert GetPayUrl(3) == "https://afdian.com/x3"
    print("[PASS] GetIfdianServerUrl / GetPayUrl 真实读取配置")

    # 3. keyboard 结构
    markdown, keyboard = BuildInterconnectPayPayload()
    rows = keyboard["content"]["rows"]
    assert len(rows) == 1 and len(rows[0]["buttons"]) == 3
    labels = [b["render_data"]["label"] for b in rows[0]["buttons"]]
    urls = [b["action"]["data"] for b in rows[0]["buttons"]]
    assert labels == ["一个月", "两个月", "三个月"], labels
    assert urls == ["https://afdian.com/x1", "https://afdian.com/x2", "https://afdian.com/x3"], urls
    print("[PASS] keyboard: 一行三按钮, URL 正确")

    # 4. 命令无参数 -> 发送 keyboard(真实配置)
    await ActivateInterconnectCommand(api=MockApi(), message=MockMessage("/激活互通"))
    assert len(POSTED) == 1 and "keyboard" in POSTED[0]
    print("[PASS] /激活互通 无参数 -> 发送 keyboard")

    # 5. 配置缺失时的默认值(临时空配置实例)
    cm2 = ConfigManager()
    cm2._config = None
    import libs.ifdianQuery as mod
    mod._config_manager = cm2  # 指向空加载的 manager
    # 用无 IfdianPayUrl 的临时文件测试
    import json, tempfile, os
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"AppId": "1", "Secret": "s", "Audit": True, "WsKey": "k"}, tmp)
    tmp.close()
    cm3 = ConfigManager(tmp.name)
    mod._config_manager = cm3
    assert GetPayUrl(1) == ""
    assert GetIfdianServerUrl() == "http://127.0.0.1:5000"  # 默认值兜底
    os.unlink(tmp.name)
    print("[PASS] 配置缺失: PayUrl 空串, ServerUrl 默认兜底")

    print("\nALL CONFIG-LOAD TESTS PASSED")


asyncio.run(main())
