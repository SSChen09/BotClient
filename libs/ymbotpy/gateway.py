# -*- coding: utf-8 -*-
import asyncio
import binascii
import traceback
import json
from .connection import ConnectionState
from fastapi import FastAPI, Request
from fastapi.responses import Response
from cryptography.hazmat.primitives.asymmetric import ed25519
from typing import Optional
from aiohttp import WSMessage, ClientWebSocketResponse, TCPConnector, ClientSession, WSMsgType
from ssl import SSLContext
from . import logging
from .connection import ConnectionSession
from .types import gateway
from .types.session import Session

_log = logging.get_logger()

class BotWebHook:
    """Bot的WebHook实现

    CODE	名称	            客户端操作       描述
    0       Dispatch	        Receive         服务端进行消息推送  x
    1       Heartbeat	        Send/Receive    客户端或服务端发送心跳  x
    2       Identify	        Send            客户端发送鉴权  x
    6       Resume	            Send            客户端恢复连接  x
    7       Reconnect	        Receive         服务端通知客户端重新连接    x
    9       Invalid Session	    Receive         当identify或resume的时候，如果参数有错，服务端会返回该消息  x
    10      Hello	            Receive         当客户端与网关建立ws连接之后，网关下发的第一条消息  x
    11      Heartbeat ACK	    Receive         当发送心跳成功之后，就会收到该消息 x
    12      HTTP Callback ACK   Reply           仅用于 http 回调模式的回包，代表机器人收到了平台推送的数据()    母鸡
    13      回调地址验证         Receive         开放平台对机器人服务端进行验证
    """

    WH_DISPATCH_EVENT = 0
    WH_HEARTBEAT = 1
    WH_IDENTITY = 2
    WH_RESUME = 6
    WH_RECONNECT = 7
    WH_INVALID_SESSION = 9
    WH_HELLO = 10
    WH_HEARTBEAT_ACK = 11
    WH_CALLBACK_ACK = 12
    WH_CALLBACK_CHECK = 13

    
    def __init__(self,appid,secret,hook_route,client,system_log,botapi,loop):

        self.appid = appid
        self.secret = secret
        self.seed_bytes = secret.encode()
        self.hook_route = hook_route
        self.bot_client = client
        self.system_log= system_log
        self.botapi = botapi
        self.loop = loop

        self.conn = ConnectionState(
                self.dispatch,self.botapi
            )

    def handle_validation(self,body: dict):

        signing_key = ed25519.Ed25519PrivateKey.from_private_bytes(self.seed_bytes)

        validation_payload = body['d']
        msg = validation_payload['event_ts'] + validation_payload['plain_token']

        signature_hex = signing_key.sign(msg.encode()).hex()

        response = {
            "plain_token": validation_payload['plain_token'],
            "signature": signature_hex
        }
        return response
    
    def verify_signature(self, headers):
        signature = headers.get("x-signature-ed25519")
        if not signature:
            return False,''
        try:
            sig = binascii.unhexlify(signature)
        except (binascii.Error, TypeError):
            return False,''
        
        if len(sig) != 64 or (sig[63] & 224) != 0:
            return False,''
        
        return True,sig


    def generate_keys(self):
        seed = self.seed_bytes
        while len(seed) < 32:
            seed = (seed * 2)[:32]
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        public_key = private_key.public_key()
        
        return public_key, private_key

    def get_signature_body(self, headers,body):
        timestamp = headers.get("x-signature-timestamp")
        if not timestamp:
            return False,''
        
        msg = timestamp.encode('utf-8') + body
       
        return True,msg


    def handle_validation_webhook(self,headers,body: dict):
        try:
            success,sig = self.verify_signature(headers)
            if not success:
                return False
            public_key, private_key = self.generate_keys()
            success, msg = self.get_signature_body(headers,body)
            if not success:
                return False
            
            public_key.verify(sig, msg)

            return True
        except:
            return False

    def dispatch(self, event, message,*args, **kwargs):
        '''
        消息分发
        '''

        method = f'on_{event}'
        print("BOT WEBHOOK MSG: %s" % message)
        try:
            coro = getattr(self.bot_client, method)
            async def runner():
                await coro(message,*args, **kwargs)
            try:
                self.loop.create_task(runner())
            except KeyboardInterrupt:
                return
        except AttributeError:
            from ymbotpy import logger
            logger.info(f"[botpy] 事件: {event} 未注册")
        

    async def init_fastapi(self):
        from ymbotpy import logger
        app = FastAPI(docs_url=None, redoc_url=None)
        @app.middleware("http")
        async def reject_unknown_routes(request: Request, call_next):
            if request.url.path != self.hook_route:
                return Response(status_code=403)  # 403 Forbidden
            response = await call_next(request)
            return response
        
        @app.api_route(self.hook_route, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
        async def qbot_callback(request: Request):
            
            if request.headers.get("x-bot-appid",'') != self.appid:
                return Response(status_code=403)
            
            if request.headers.get("user-agent",'') != "QQBot-Callback":
                return Response(status_code=403)
            
            if 'x-signature-ed25519' not in request.headers:
                return Response(status_code=403)
            
            if 'x-signature-timestamp' not in request.headers:
                return Response(status_code=403)
            
            try:
                bytes_body = await request.body()
                body = json.loads(bytes_body.decode('utf-8'))
            except Exception as e:
                logger.warning(e)
                return
            
            if self.system_log:
                logger.warning(body)

            if body.get("op") == self.WH_CALLBACK_CHECK:
                return self.handle_validation(body)
            
            if not self.handle_validation_webhook(request.headers,bytes_body):
                logger.error(f"签名校验不通过: {request.headers}")
                return Response(status_code=403)
            
            event = body.get("t").lower()
            parser = self.conn.parsers.get(event)
            if parser is not None:
                parser(body)
            else:
                logger.warning(f"未注册的事件类型: {event}")
            
        return app


class BotWebSocket:
    """Bot的Websocket实现

    CODE	名称	客户端操作	描述
    0	Dispatch	Receive	服务端进行消息推送
    1	Heartbeat	Send/Receive	客户端或服务端发送心跳
    2	Identify	Send	客户端发送鉴权
    6	Resume	Send	客户端恢复连接
    7	Reconnect	Receive	服务端通知客户端重新连接
    9	Invalid Session	Receive	当identify或resume的时候，如果参数有错，服务端会返回该消息
    10	Hello	Receive	当客户端与网关建立ws连接之后，网关下发的第一条消息
    11	Heartbeat ACK	Receive	当发送心跳成功之后，就会收到该消息
    """

    WS_DISPATCH_EVENT = 0
    WS_HEARTBEAT = 1
    WS_IDENTITY = 2
    WS_RESUME = 6
    WS_RECONNECT = 7
    WS_INVALID_SESSION = 9
    WS_HELLO = 10
    WS_HEARTBEAT_ACK = 11

    def __init__(self, session: Session, _connection: ConnectionSession):
        self._conn: Optional[ClientWebSocketResponse] = None
        self._session = session
        self._connection = _connection
        self._parser = _connection.parser
        self._can_reconnect = True
        self._INVALID_RECONNECT_CODE = [9001, 9005]
        self._AUTH_FAIL_CODE = [4004]

    async def on_error(self, exception: BaseException):
        _log.error("[botpy] websocket连接: %s, 异常信息 : %s" % (self._conn, exception))
        traceback.print_exc()
        self._connection.add(self._session)

    async def on_closed(self, close_status_code, close_msg):
        _log.info("[botpy] 关闭, 返回码: %s" % close_status_code + ", 返回信息: %s" % close_msg)
        if close_status_code in self._AUTH_FAIL_CODE:
            _log.info("[botpy] 鉴权失败，重置token...")
            self._session["token"].access_token = None
        # 这种不能重新链接
        if close_status_code in self._INVALID_RECONNECT_CODE or not self._can_reconnect:
            _log.info("[botpy] 无法重连，创建新连接!")
            self._session["session_id"] = ""
            self._session["last_seq"] = 0
        # 断连后启动一个新的链接并透传当前的session，不使用内部重连的方式，避免死循环
        self._connection.add(self._session)

    async def on_message(self, ws, message):
        _log.debug("[botpy] 接收消息: %s" % message)
        print("[botpy] 接收消息: %s" % message)
        msg = json.loads(message)

        if await self._is_system_event(msg, ws):
            return

        event = msg.get("t")
        opcode = msg.get("op")
        event_seq = msg["s"]
        if event_seq > 0:
            self._session["last_seq"] = event_seq

        if event == "READY":
            # 心跳检查
            self._connection.loop.create_task(self._send_heart(interval=30))
            ready = await self._ready_handler(msg)
            _log.info(f"[botpy] 机器人「{ready['user']['username']}」启动成功！")

        if event == "RESUMED":
            # 心跳检查
            self._connection.loop.create_task(self._send_heart(interval=30))
            _log.info("[botpy] 机器人重连成功! ")

        if event and opcode == self.WS_DISPATCH_EVENT:
            event = msg["t"].lower()
            try:
                func = self._parser[event]
            except KeyError:
                _log.error("_parser unknown event %s.", event)
            else:
                func(msg)

    async def on_connected(self, ws: ClientWebSocketResponse):
        self._conn = ws
        if self._conn is None:
            raise Exception("[botpy] websocket连接失败")
        if self._session["session_id"]:
            await self.ws_resume()
        else:
            await self.ws_identify()

    async def ws_connect(self):
        """
        websocket向服务器端发起链接，并定时发送心跳
        """

        _log.info("[botpy] 启动中...")
        ws_url = self._session["url"]
        if not ws_url:
            raise Exception("[botpy] 会话url为空")

        # adding SSLContext-containing connector to prevent SSL certificate verify failed error
        async with ClientSession(connector=TCPConnector(limit=10, ssl=SSLContext())) as session:
            async with session.ws_connect(self._session["url"]) as ws_conn:
                while True:
                    msg: WSMessage
                    msg = await ws_conn.receive()
                    if msg.type == WSMsgType.TEXT:
                        await self.on_message(ws_conn, msg.data)
                    elif msg.type == WSMsgType.ERROR:
                        await self.on_error(ws_conn.exception())
                        await ws_conn.close()
                    elif msg.type == WSMsgType.CLOSED or msg.type == WSMsgType.CLOSE:
                        await self.on_closed(ws_conn.close_code, msg.extra)
                    if ws_conn.closed:
                        _log.info("[botpy] ws关闭, 停止接收消息!")
                        break

    async def ws_identify(self):
        """websocket鉴权"""
        if not self._session["intent"]:
            self._session["intent"] = 1

        _log.info("[botpy] 鉴权中...")
        await self._session["token"].check_token()
        payload = {
            "op": self.WS_IDENTITY,
            "d": {
                "shard": [
                    self._session["shards"]["shard_id"],
                    self._session["shards"]["shard_count"],
                ],
                "token": self._session["token"].get_string(),
                "intents": self._session["intent"],
            },
        }

        await self.send_msg(json.dumps(payload))

    async def send_msg(self, event_json):
        """
        websocket发送消息
        :param event_json:
        """
        send_msg = event_json
        _log.debug("[botpy] 发送消息: %s" % send_msg)
        if isinstance(self._conn, ClientWebSocketResponse):
            if self._conn.closed:
                _log.debug("[botpy] ws连接已关闭! ws对象: %s" % self._conn)
            else:
                await self._conn.send_str(data=send_msg)

    async def ws_resume(self):
        """
        websocket重连
        """
        _log.info("[botpy] 重连启动...")
        await self._session["token"].check_token()
        payload = {
            "op": self.WS_RESUME,
            "d": {
                "token": self._session["token"].get_string(),
                "session_id": self._session["session_id"],
                "seq": self._session["last_seq"],
            },
        }

        await self.send_msg(json.dumps(payload))

    async def _ready_handler(self, message_event) -> gateway.ReadyEvent:
        data = message_event["d"]
        self.version = data["version"]
        self._session["session_id"] = data["session_id"]
        self._session["shards"]["shard_id"] = data["shard"][0]
        self._session["shards"]["shard_count"] = data["shard"][1]
        self.user = data["user"]
        return data

    async def _is_system_event(self, message_event, ws):
        """
        系统事件
        :param message_event:消息
        :param ws:websocket
        :return:
        """
        event_op = message_event["op"]
        if event_op == self.WS_HELLO:
            await self.on_connected(ws)
            return True
        if event_op == self.WS_HEARTBEAT_ACK:
            return True
        if event_op == self.WS_RECONNECT:
            self._can_reconnect = True
            return True
        if event_op == self.WS_INVALID_SESSION:
            self._can_reconnect = False
            return True
        return False

    async def _send_heart(self, interval):
        """
        心跳包
        :param interval: 间隔时间
        """
        _log.info("[botpy] 心跳维持启动...")
        while True:
            payload = {
                "op": self.WS_HEARTBEAT,
                "d": self._session["last_seq"],
            }

            if self._conn is None:
                _log.debug("[botpy] 连接已关闭!")
                return
            if self._conn.closed:
                _log.debug("[botpy] ws连接已关闭, 心跳检测停止，ws对象: %s" % self._conn)
                return

            await self.send_msg(json.dumps(payload))
            await asyncio.sleep(interval)