"""Read model discovery on demand, outside a database transaction or generation."""
import asyncio
import threading
import time
from .codex import CodexAppServer
from .contracts import new_id

_cache = {}
_lock = threading.Lock()


def models(settings, login_mode, *, refresh=False):
    key = (str(settings.model_runtime), login_mode)
    with _lock:
        cached = _cache.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            return cached[1]

        async def discover():
            from .service import DomainError
            directory = settings.path("model-catalog", new_id())
            directory.parent.mkdir(exist_ok=True)
            session = CodexAppServer(directory, existing_login=login_mode == "existing")
            try:
                await session.start()
                account = await session.request("account/read", {"refreshToken": False})
                if not account.get("account"):
                    raise DomainError("needs_login", "请先选择已登录的 Codex 登录方式。", 409)
                result, cursor = [], None
                while True:
                    page = await session.request("model/list", {"limit": 100, **({"cursor": cursor} if cursor else {})})
                    result.extend({"id": m["id"], "name": m.get("displayName") or m["id"], "is_default": bool(m.get("isDefault"))} for m in page["data"])
                    cursor = page.get("nextCursor")
                    if not cursor or len(result) >= 1000:
                        break
                return {"models": result, "login_mode": login_mode}
            finally:
                await session.close()

        try:
            result = asyncio.run(asyncio.wait_for(discover(), timeout=35))
        except (TimeoutError, ConnectionError, RuntimeError) as error:
            from .service import DomainError
            raise DomainError("model_catalog_unavailable", "读取模型列表失败，请检查登录或连接后重试。", 503) from error
        _cache[key] = (time.monotonic(), result)
        return result
