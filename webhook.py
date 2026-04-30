"""Discord Webhook管理模块"""
import asyncio
import re
import urllib.parse
import aiohttp
from astrbot.api import logger
from astrbot.core.star.star import star_map

try:
    import discord
    HAS_DISCORD = True
    # 检查是否为 py-cord 而非已停止维护的 discord.py
    if not hasattr(discord, "__version__") or "py-cord" not in getattr(discord, "__version__", "").lower():
        logger.warning(
            "检测到 discord.py 而非 py-cord，可能存在 API 不兼容。"
            "建议卸载后重新安装: pip uninstall discord.py && pip install py-cord>=2.4.0"
        )
except ImportError:
    HAS_DISCORD = False
    logger.info("py-cord 未安装，Webhook 自动创建功能不可用。如需使用，请执行: pip install py-cord>=2.4.0")

# Discord API 限制
MAX_CONTENT_LENGTH = 2000
MAX_USERNAME_LENGTH = 80
# Discord 禁止的用户名子串（大小写不敏感）
FORBIDDEN_USERNAME_SUBSTRINGS = ("discord", "clyde")
REQUEST_TIMEOUT_SECONDS = 30


class DiscordWebhookManager:
    """Discord Webhook管理器"""

    def __init__(self, context=None):
        self._discord_client = None
        self._context = context
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
                self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """释放 aiohttp session 资源"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def set_context(self, context):
        """设置context，用于获取Discord客户端"""
        self._context = context

    def set_discord_client(self, client):
        """显式设置 Discord 客户端实例（替代自动搜索）"""
        self._discord_client = client

    def _try_extract_discord_client(self, platform_inst) -> bool:
        """检查 platform_inst 是否持有 Discord 客户端，若是则缓存"""
        if not hasattr(platform_inst, 'client'):
            return False
        try:
            client = platform_inst.client
            if hasattr(client, 'user') and client.user and hasattr(client, 'get_channel'):
                self._discord_client = client
                return True
        except Exception as e:
            logger.debug(f"提取Discord客户端时出错: {e}")
        return False

    def _search_platform_insts(self, platform_manager) -> bool:
        """在 platform_manager.platform_insts 中搜索 Discord 客户端"""
        if not hasattr(platform_manager, 'platform_insts'):
            return False
        insts = platform_manager.platform_insts
        if isinstance(insts, dict):
            for inst in insts.values():
                if self._try_extract_discord_client(inst):
                    return True
        elif isinstance(insts, list):
            for inst in insts:
                if self._try_extract_discord_client(inst):
                    return True
        return False

    def _get_discord_client(self):
        """获取Discord客户端实例"""
        if not HAS_DISCORD:
            return None

        if self._discord_client is not None:
            return self._discord_client

        # 优先从当前 context 查找
        if self._context and hasattr(self._context, 'platform_manager'):
            if self._search_platform_insts(self._context.platform_manager):
                return self._discord_client

        # 回退：遍历所有 star 实例
        logger.debug("通过 star_map 搜索 Discord 客户端")
        for star_instance in star_map.values():
            if hasattr(star_instance, 'context') and hasattr(star_instance.context, 'platform_manager'):
                if self._search_platform_insts(star_instance.context.platform_manager):
                    return self._discord_client

        logger.debug("未找到 Discord 客户端")
        return None

    def get_discord_client(self):
        """公开获取Discord客户端实例"""
        return self._get_discord_client()

    async def create_webhook_for_channel(self, channel_id: int, webhook_name: str = "MsgTransfer Bot") -> str | None:
        """为指定频道自动创建Webhook"""
        if not HAS_DISCORD:
            logger.error("未安装discord库，无法自动创建Webhook")
            return None

        client = self._get_discord_client()
        if not client:
            logger.error("无法获取Discord客户端")
            return None

        try:
            channel = client.get_channel(channel_id)
            if not channel:
                logger.error(f"无法获取频道 {channel_id}")
                return None

            if not hasattr(channel, 'create_webhook'):
                logger.error(f"频道 {channel_id} 不支持创建Webhook")
                return None

            webhook = await channel.create_webhook(
                name=self._sanitize_username(webhook_name),
                reason="自动创建用于消息转发的Webhook"
            )
            logger.info(f"成功为频道 {channel_id} 创建Webhook")
            return webhook.url

        except Exception as e:
            if HAS_DISCORD and isinstance(e, discord.Forbidden):
                logger.error(f"机器人在频道 {channel_id} 没有创建Webhook的权限")
            elif HAS_DISCORD and isinstance(e, discord.HTTPException):
                logger.error(f"创建Webhook时发生HTTP错误: {e}")
            else:
                logger.error(f"创建Webhook时发生未知错误: {e}")
            return None

    @staticmethod
    def get_qq_avatar_url(qq_id: str) -> str:
        """获取QQ用户头像URL"""
        return f"http://q1.qlogo.cn/g?b=qq&nk={qq_id}&s=100"

    @staticmethod
    def get_default_avatar_url() -> str:
        """获取默认头像URL"""
        return "https://cdn.discordapp.com/embed/avatars/0.png"

    @staticmethod
    def get_avatar_url(platform: str, user_id: str) -> str:
        """根据平台获取用户头像URL"""
        if platform == "aiocqhttp":
            return DiscordWebhookManager.get_qq_avatar_url(user_id)
        # Discord 头像需要 avatar_hash，仅有 user_id 无法构造有效 URL，统一回退到默认
        return DiscordWebhookManager.get_default_avatar_url()

    @staticmethod
    def _sanitize_username(name: str) -> str:
        """清理用户名，符合 Discord Webhook 要求

        - 不超过 80 字符
        - 不包含 'discord' 或 'clyde'（大小写不敏感）
        """
        # 截断
        name = name[:MAX_USERNAME_LENGTH]
        # 替换禁止的子串为相似字符
        for forbidden in FORBIDDEN_USERNAME_SUBSTRINGS:
            # 大小写不敏感替换
            name = re.sub(forbidden, lambda m: m.group(0)[0] + '​' + m.group(0)[1:], name, flags=re.IGNORECASE)
        return name

    @staticmethod
    def _truncate_content(content: str, max_len: int = MAX_CONTENT_LENGTH) -> str:
        """截断消息内容至 Discord 限制长度"""
        if len(content) <= max_len:
            return content
        # 保留前 max_len-1 个字符并在末尾添加截断标记
        return content[:max_len - 1] + "…"

    @staticmethod
    def format_message_content(message_chain) -> str:
        """格式化消息内容为Discord可读的文本"""
        text_parts = []
        extra_lines = []
        for component in message_chain:
            if hasattr(component, 'text') and component.text:
                text_parts.append(component.text)
            elif hasattr(component, 'qq') and component.qq:
                text_parts.append(f"<@{component.qq}>")
            elif component.__class__.__name__ == "Image":
                img_url = getattr(component, 'url', None)
                if img_url:
                    extra_lines.append(img_url)
                else:
                    text_parts.append("[图片]")
            elif component.__class__.__name__ == "File":
                name = component.name or "文件"
                file_url = component.url or ""
                if file_url:
                    # 确保 fname 参数包含文件名
                    parsed = urllib.parse.urlparse(file_url)
                    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    if 'fname' in qs and not qs['fname'][0]:
                        qs['fname'] = [name]
                        new_query = urllib.parse.urlencode(qs, doseq=True)
                        file_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
                    extra_lines.append(f"[{name}]({file_url})")
                else:
                    text_parts.append(f"[{name}]")
            elif hasattr(component, 'url') and component.url:
                url = str(component.url)[:500]
                extra_lines.append(url)
            elif hasattr(component, 'src') and component.src:
                extra_lines.append(str(component.src)[:500])
        content = ''.join(text_parts)
        if extra_lines:
            if content and not content.endswith('\n'):
                content += '\n'
            content += '\n'.join(extra_lines)
        return content

    async def send_webhook_message(
        self,
        webhook_url: str,
        username: str,
        avatar_url: str,
        content: str,
    ) -> str | None:
        """发送消息到Discord Webhook，返回Discord消息ID（失败返回None）"""
        try:
            if not content:
                content = "​"  # zero-width space, prevents Discord "message cannot be empty" rejection

            username = self._sanitize_username(username)
            content = self._truncate_content(content)

            payload = {
                "content": content,
                "username": username,
                "avatar_url": avatar_url,
                "allowed_mentions": {"parse": ["users"]},
            }

            session = await self._get_session()
            async with session.post(webhook_url, json=payload, params={"wait": "true"}) as resp:
                if resp.status not in (200, 201, 204):
                    body = await resp.text()
                    logger.error(
                        f"Webhook发送失败 [HTTP {resp.status}]: {body[:500]}"
                    )
                    return None
                if resp.status == 204:
                    return None
                data = await resp.json()
                return data.get("id")
        except Exception as e:
            logger.error(f"Webhook发送异常: {e}")
            return None

    @staticmethod
    def build_virtual_username(sender_name: str, source_platform: str) -> str:
        """构建虚拟用户名"""
        platform_map = {
            "aiocqhttp": "QQ",
            "discord": "Discord",
        }
        platform_name = platform_map.get(source_platform, source_platform)
        return f"{sender_name} ({platform_name})"
