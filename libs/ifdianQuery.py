# -*- coding: utf-8 -*-
"""爱发电订单激活与群服互通查询模块。

封装与爱发电互通服务(.codewhale/docs/ifdianQueryDocs.md)的 HTTP 交互，
提供:
- 群聊指令 /激活互通 <订单号>: 按订单激活本群互通并叠加有效期
- 群聊指令 /查询互通: 查询本群互通有效期
- C2C 后门指令 /设置互通 <群号> <天数> <key>: 直接设置指定群的有效天数

服务端默认地址 http://127.0.0.1:5000,可通过 config.json 的
"IfdianServerUrl" 配置项覆盖。

购买按钮跳转地址通过 config.json 的 "IfdianPayUrl" 配置:
- 字符串形式: 三个按钮共用同一个 URL
- 对象形式: {"1": "一个月链接", "2": "两个月链接", "3": "三个月链接"}
"""

import json

import aiohttp

from ymbotpy import logging
from ymbotpy.message import C2CMessage, GroupMessage
from ymbotpy.types.inline import Action, Button, Keyboard, KeyboardRow, Permission, RenderData
from ymbotpy.types.message import KeyboardPayload, MarkdownPayload

from libs.basic import SplitCommandParams
from libs.command_util import Commands
from libs.configManager import ConfigManager

DEFAULT_IFDIAN_SERVER_URL = "http://127.0.0.1:5000"
REQUEST_TIMEOUT_SECONDS = 5
INTERCONNECT_PAY_OPTIONS = ((1, "一个月"), (2, "两个月"), (3, "三个月"))
INTERCONNECT_PAY_MARKDOWN = (
    "# 激活互通\n"
    "请选择要购买的互通时长，点击下方按钮前往下单:\n\n"
    "下单完成后，回到**需要激活群**发送 `/激活互通 <订单号>` 即可激活并叠加有效期\n\n"
    "如有疑问请联系HuHoBot管理员"
)

_log = logging.get_logger()
_config_manager = ConfigManager()


def GetIfdianServerUrl() -> str:
    """返回爱发电互通服务地址。"""
    url = _config_manager.Get("IfdianServerUrl", "")
    return (str(url).rstrip("/") or DEFAULT_IFDIAN_SERVER_URL)


def GetPayUrl(months: int) -> str:
    """读取指定月数对应的爱发电购买链接(支持统一 URL 或按月映射)。"""
    config = _config_manager.Get("IfdianPayUrl", "")
    if isinstance(config, dict):
        return str(config.get(str(months)) or config.get(months) or "")
    return str(config or "")


def BuildInterconnectPayPayload() -> tuple[MarkdownPayload, KeyboardPayload]:
    """构建互通购买时长选择表单: 一行三个 URL 跳转按钮(一个月/两个月/三个月)。"""
    buttons = [
        Button(
            id=f"ifdian_pay_{months}",
            render_data=RenderData(label=label, visited_label=label, style=1),
            action=Action(
                type=0,
                permission=Permission(type=2),
                click_limit=1,
                data=GetPayUrl(months),
                at_bot_show_channel_list=False,
            ),
        )
        for months, label in INTERCONNECT_PAY_OPTIONS
    ]
    markdown = MarkdownPayload(content=INTERCONNECT_PAY_MARKDOWN)
    keyboard = KeyboardPayload(content=Keyboard(rows=[KeyboardRow(buttons=buttons)]))
    return markdown, keyboard


async def _PostJson(path: str, payload: dict) -> dict:
    """向服务端发送 JSON POST 请求并解析响应。"""
    url = f"{GetIfdianServerUrl()}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                return json.loads(await response.text())
    except Exception as exc:
        _log.error(f"POST {path} 请求失败: {exc}")
        return {"code": -1, "msg": "无法连接互通服务，请稍后重试"}


async def _GetJson(path: str, params: dict) -> dict:
    """向服务端发送 GET 请求并解析响应。"""
    url = f"{GetIfdianServerUrl()}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                return json.loads(await response.text())
    except Exception as exc:
        _log.error(f"GET {path} 请求失败: {exc}")
        return {"code": -1, "msg": "无法连接互通服务，请稍后重试"}


async def ActivateOrder(order_no: str, group_openid: str) -> dict:
    """按订单号激活指定群的互通有效期(POST /api/order/activate)。"""
    return await _PostJson(
        "/api/order/activate",
        {"order_no": order_no, "group_openid": group_openid},
    )


async def QueryGroupStatus(group_openid: str) -> dict:
    """查询指定群的互通有效期(GET /api/group/status)。"""
    return await _GetJson("/api/group/status", {"group_openid": group_openid})


async def SetGroupInterconnect(group_openid: str, days: int, token: str) -> dict:
    """管理员后门: 直接设置指定群的有效天数(POST /set, 覆盖不叠加)。"""
    return await _PostJson(
        "/set",
        {"group_openid": group_openid, "days": days, "token": token},
    )


async def IsGroupActive(group_openid: str) -> bool:
    """检查群是否已激活且处于有效期内(服务端 code == 200)。"""
    result = await QueryGroupStatus(group_openid)
    return isinstance(result, dict) and result.get("code") == 200


def _FormatData(data: dict) -> str:
    """从 data 中提取到期时间与剩余天数的展示片段。"""
    parts = []
    expire_time = (data or {}).get("expire_time")
    remaining_days = (data or {}).get("remaining_days")
    if expire_time:
        parts.append(f"到期时间: {expire_time}")
    if remaining_days is not None:
        parts.append(f"剩余 {remaining_days} 天")
    return "，".join(parts)


def BuildActivateFeedback(result: dict) -> str:
    """构造 /激活互通 的反馈文本。"""
    code = result.get("code")
    msg = result.get("msg", "")
    if code == 200:
        data_text = _FormatData(result.get("data") or {})
        return f"激活成功: {msg}" + (f"，{data_text}" if data_text else "")
    return f"激活失败: {msg}"


def BuildStatusFeedback(result: dict) -> str:
    """构造 /查询互通 的反馈文本。"""
    code = result.get("code")
    msg = result.get("msg", "")
    if code == 200:
        data_text = _FormatData(result.get("data") or {})
        return f"本群互通正常: {msg}" + (f"，{data_text}" if data_text else "")
    return f"本群未开通互通: {msg}"


def BuildSetFeedback(result: dict) -> str:
    """构造 /设置互通 的反馈文本。"""
    code = result.get("code")
    msg = result.get("msg", "")
    if code == 200:
        data_text = _FormatData(result.get("data") or {})
        return f"设置成功: {msg}" + (f"，{data_text}" if data_text else "")
    return f"设置失败: {msg}"


@Commands("激活互通")
async def ActivateInterconnectCommand(api, message: GroupMessage, params=None):
    """群聊: 无参数时发送购买时长选择按钮，有参数时按订单号激活本群互通。"""
    param_list = SplitCommandParams(params)
    if not param_list:
        if not any(GetPayUrl(months) for months, _ in INTERCONNECT_PAY_OPTIONS):
            await message.reply(content="购买链接未配置，请联系管理员在 config.json 中填写 IfdianPayUrl")
            return True
        markdown, keyboard = BuildInterconnectPayPayload()
        await api.post_group_message(
            group_openid=message.group_openid,
            msg_type=2,
            msg_id=message.id,
            msg_seq=2,
            markdown=markdown,
            keyboard=keyboard,
        )
        return True
    order_no = param_list[0]
    result = await ActivateOrder(order_no, message.group_openid)
    await message.reply(content=BuildActivateFeedback(result))
    return True


@Commands("查询互通")
async def QueryInterconnectCommand(api, message: GroupMessage, params=None):
    """群聊: 查询本群互通是否激活及其有效期。"""
    result = await QueryGroupStatus(message.group_openid)
    await message.reply(content=BuildStatusFeedback(result))
    return True


@Commands("设置互通")
async def SetInterconnectCommand(api, message: C2CMessage, params=None):
    """C2C(仅管理员): 直接设置指定群的有效天数，格式: /设置互通 <群号> <天数> <key>。"""
    param_list = SplitCommandParams(params)
    if len(param_list) < 3:
        await message.reply(content="参数不正确，格式: /设置互通 <群号> <天数> <key>")
        return True
    group_openid, days, token = param_list[0], param_list[1], param_list[2]
    result = await SetGroupInterconnect(group_openid, days, token)
    await message.reply(content=BuildSetFeedback(result))
    return True


__all__ = [
    "ActivateInterconnectCommand",
    "ActivateOrder",
    "BuildActivateFeedback",
    "BuildInterconnectPayPayload",
    "BuildSetFeedback",
    "BuildStatusFeedback",
    "GetIfdianServerUrl",
    "GetPayUrl",
    "INTERCONNECT_PAY_OPTIONS",
    "IsGroupActive",
    "QueryGroupStatus",
    "QueryInterconnectCommand",
    "SetGroupInterconnect",
    "SetInterconnectCommand",
]
