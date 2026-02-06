"""Discord Webhook管理模块"""
import aiohttp
from astrbot.api import logger
from astrbot.core.star.star import star_map

import discord  # Pycord

try:
    import discord
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False
    logger.warning("未安装py-cord库，自动创建Webhook功能不可用")


class DiscordWebhookManager:
    """Discord Webhook管理器 (Pycord版)"""
    
    def __init__(self, context=None):
        self._discord_client = None
        self._context = context
    
    def set_context(self, context):
        """设置context，用于获取Discord客户端"""
        self._context = context
    
    def _get_discord_client(self):
        """获取Discord客户端实例"""
        if not HAS_DISCORD:
            return None
        
        if self._discord_client is None:
            # 首先尝试从设置的context获取
            if self._context and hasattr(self._context, 'platform_manager'):
                platform_manager = self._context.platform_manager
                
                # 使用正确的属性名：platform_insts
                if hasattr(platform_manager, 'platform_insts'):
                    platform_insts = platform_manager.platform_insts
                    
                    # platform_insts可能是列表或字典
                    if isinstance(platform_insts, dict):
                        for platform_id, platform_inst in platform_insts.items():
                            if hasattr(platform_inst, 'client'):
                                try:
                                    if hasattr(platform_inst.client, 'user') and platform_inst.client.user:
                                        if hasattr(platform_inst.client, 'create_webhook'):
                                            self._discord_client = platform_inst.client
                                            return self._discord_client
                                except Exception:
                                    pass
                    elif isinstance(platform_insts, list):
                        for platform_inst in platform_insts:
                            try:
                                # 直接检查是否是DiscordPlatformAdapter
                                if 'DiscordPlatformAdapter' in str(type(platform_inst)):
                                    if hasattr(platform_inst, 'client'):
                                        self._discord_client = platform_inst.client
                                        return self._discord_client
                            except Exception:
                                pass
            
            # 如果从context获取失败，尝试从star_map获取
            if self._discord_client is None:
                for star_instance in star_map.values():
                    if hasattr(star_instance, 'context') and hasattr(star_instance.context, 'platform_manager'):
                        platform_manager = star_instance.context.platform_manager
                        
                        if hasattr(platform_manager, 'platform_insts'):
                            platform_insts = platform_manager.platform_insts
                            
                            if isinstance(platform_insts, dict):
                                for platform_id, platform_inst in platform_insts.items():
                                    if hasattr(platform_inst, 'client'):
                                        try:
                                            if hasattr(platform_inst.client, 'user') and platform_inst.client.user:
                                                if hasattr(platform_inst.client, 'create_webhook'):
                                                    self._discord_client = platform_inst.client
                                                    return self._discord_client
                                        except Exception:
                                            pass
                            elif isinstance(platform_insts, list):
                                for platform_inst in platform_insts:
                                    try:
                                        if 'DiscordPlatformAdapter' in str(type(platform_inst)):
                                            if hasattr(platform_inst, 'client'):
                                                self._discord_client = platform_inst.client
                                                return self._discord_client
                                    except Exception:
                                        pass
        
        return self._discord_client
    
    async def create_webhook_for_channel(self, channel_id: int, webhook_name: str = "MsgTransfer Bot") -> str | None:
        """为指定频道自动创建Webhook
        
        Args:
            channel_id: Discord频道ID
            webhook_name: Webhook名称
            
        Returns:
            Webhook URL，如果创建失败返回None
        """
        if not HAS_DISCORD:
            logger.error("❌ 未安装py-cord库，无法自动创建Webhook")
            return None
        
        client = self._get_discord_client()
        
        if not client:
            logger.error("❌ 无法获取Discord客户端")
            return None
        
        try:
            # 获取频道对象
            channel = client.get_channel(channel_id)
            if not channel:
                logger.error(f"❌ 无法获取频道 {channel_id}")
                return None
            
            # 创建Webhook
            webhook = await channel.create_webhook(
                name=webhook_name,
                reason="自动创建用于消息转发的Webhook"
            )
            
            logger.info(f"✅ 成功为频道 {channel_id} 创建Webhook")
            return webhook.url
            
        except discord.Forbidden:
            logger.error(f"❌ 机器人在频道 {channel_id} 没有创建Webhook的权限")
            return None
        except discord.HTTPException as e:
            logger.error(f"❌ 创建Webhook时发生HTTP错误: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ 创建Webhook时发生未知错误: {e}")
            return None
    
    @staticmethod
    def get_qq_avatar_url(qq_id: str) -> str:
        """获取QQ用户头像URL"""
        return f"http://q1.qlogo.cn/g?b=qq&nk={qq_id}&s=100"
    
    @staticmethod
    def get_discord_avatar_url(discord_id: str) -> str:
        """获取Discord用户头像URL"""
        return f"https://cdn.discordapp.com/avatars/{discord_id}/default.png"
    
    @staticmethod
    def get_default_avatar_url() -> str:
        """获取默认头像URL"""
        return "https://cdn.discordapp.com/embed/avatars/0.png"
    
    @staticmethod
    def get_avatar_url(platform: str, user_id: str) -> str:
        """获取用户头像URL"""
        if platform == "aiocqhttp":
            return DiscordWebhookManager.get_qq_avatar_url(user_id)
        elif platform == "discord":
            return DiscordWebhookManager.get_discord_avatar_url(user_id)
        else:
            return DiscordWebhookManager.get_default_avatar_url()
    
    @staticmethod
    def format_message_content(message_chain) -> str:
        """格式化消息内容为文本+图片分离，图片url单独一行，文件链接自动补全fname参数，保证下载体验"""
        text_parts = []
        image_urls = []
        for component in message_chain:
            # 处理文本
            if hasattr(component, 'text') and component.text:
                text_parts.append(component.text)
            # 处理@消息
            elif hasattr(component, 'qq') and component.qq:
                text_parts.append(f"<@{component.qq}>")
            # 处理文件类型，自动补全fname参数
            elif hasattr(component, 'file') and hasattr(component.file, 'url') and component.file.url:
                file_url = component.file.url
                file_name = getattr(component.file, "name", None)
                # 针对QQ/企微等ftn_handler直链，自动补全fname参数
                if file_name and 'ftn.qq.com/ftn_handler/' in file_url:
                    if 'fname=' not in file_url or file_url.endswith('fname='):
                        from urllib.parse import quote
                        safe_name = quote(file_name)
                        if '?' in file_url:
                            file_url = file_url.split('?')[0] + f'?fname={safe_name}'
                        else:
                            file_url = file_url + f'?fname={safe_name}'
                text_parts.append(f"[文件：{file_name}]({file_url})\n{file_url}" if file_name else file_url)
            # 处理URL（图片/文件/资源）
            elif hasattr(component, 'url') and component.url:
                image_urls.append(component.url)
            elif hasattr(component, 'src') and component.src:
                image_urls.append(component.src)
        # 文本和图片分开，图片url每个单独一行
        content = ''.join(text_parts)
        if image_urls:
            if content and not content.endswith('\n'):
                content += '\n'
            content += '\n'.join(image_urls)
        return content
    
    @staticmethod
    async def send_webhook_message(
        webhook_url: str,
        username: str,
        avatar_url: str,
        content: str
    ) -> bool:
        """发送消息到Discord Webhook
        
        Args:
            webhook_url: Discord Webhook URL
            username: 虚拟用户名
            avatar_url: 虚拟用户头像URL
            content: 消息内容（Discord会自动识别URL并显示图片）
            
        Returns:
            bool: 是否发送成功
        """
        try:
            # Discord不允许content和embeds都为空，但允许content为空
            if not content:
                content = "\u200b"  # 零宽空格
            
            payload = {
                "content": content,
                "username": username,
                "avatar_url": avatar_url
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status in [200, 204, 201]:  # 200, 204, 201都表示成功
                        return True
                    else:
                        return False
        except Exception as e:
            logger.error(f"❌ Webhook发送异常: {e}")
            return False
    
    @staticmethod
    def build_virtual_username(sender_name: str, source_platform: str) -> str:
        """构建虚拟用户名"""
        platform_map = {
            "aiocqhttp": "QQ",
            "discord": "Discord",
            "wechatpadpro": "微信",
            "telegram": "Telegram"
        }
        platform_name = platform_map.get(source_platform, source_platform)
        return f"{sender_name} ({platform_name})"