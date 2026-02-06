"""Discord Webhook管理模块"""
import aiohttp
import json
from pathlib import Path
from astrbot.api import logger
from astrbot.core.star.star import star_map

try:
    import discord
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False
    logger.warning("未安装discord库，自动创建Webhook功能不可用")


class UserMappingManager:
    """用户映射管理器"""
    
    def __init__(self, mapping_file: str = "user_mapping.json"):
        self.mapping_file = Path(mapping_file)
        self.mappings = {}
        self.load_mappings()
    
    def load_mappings(self):
        """加载用户映射"""
        try:
            if self.mapping_file.exists():
                with open(self.mapping_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.mappings = data.get('mappings', {})
                logger.info(f"✅ 已加载用户映射，共 {len(self.mappings)} 条")
            else:
                self.mappings = {}
                logger.info("📝 用户映射文件不存在，将创建新的映射文件")
        except Exception as e:
            logger.error(f"❌ 加载用户映射失败: {e}")
            self.mappings = {}
    
    def save_mappings(self):
        """保存用户映射"""
        try:
            data = {
                "mappings": self.mappings
            }
            with open(self.mapping_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ 已保存用户映射，共 {len(self.mappings)} 条")
        except Exception as e:
            logger.error(f"❌ 保存用户映射失败: {e}")
    
    def add_mapping(self, original_platform: str, original_user_id: str, target_platform: str, target_user_id: str):
        """添加用户映射"""
        key = f"{original_platform}:{original_user_id}:{target_platform}"
        self.mappings[key] = target_user_id
        self.save_mappings()
        logger.info(f"📝 已添加映射: {key} -> {target_user_id}")
    
    def get_mapped_user_id(self, original_platform: str, original_user_id: str, target_platform: str) -> str:
        """获取映射后的用户ID"""
        key = f"{original_platform}:{original_user_id}:{target_platform}"
        return self.mappings.get(key, original_user_id)  # 如果没有映射，返回原始ID
    
    def create_mapping_if_not_exists(self, original_platform: str, original_user_id: str, target_platform: str, target_user_id: str):
        """如果映射不存在则自动创建"""
        key = f"{original_platform}:{original_user_id}:{target_platform}"
        if key not in self.mappings:
            self.add_mapping(original_platform, original_user_id, target_platform, target_user_id)
            logger.info(f"📝 自动创建映射: {original_platform}:{original_user_id} -> {target_platform}:{target_user_id}")
            return True
        return False
    
    @staticmethod
    def convert_at_mentions(text: str, user_mapping_manager, reverse_direction: bool = False) -> str:
        """转换@消息格式
        
        Args:
            text: 原始文本
            user_mapping_manager: 用户映射管理器实例
            reverse_direction: 是否反向转换（Discord->QQ）
        """
        import re
        
        if reverse_direction:
            # Discord -> QQ: 将 <@123456789> 转换为 @QQ用户名
            def replace_discord_at(match):
                discord_id = match.group(1)
                # 查找映射的QQ用户ID
                for key, mapped_id in user_mapping_manager.mappings.items():
                    parts = key.split(":")
                    if (len(parts) == 3 and parts[0] == "discord" and 
                        parts[2] == "aiocqhttp" and mapped_id == discord_id):
                        # 找到Discord用户映射到QQ的情况
                        original_qq_id = parts[1]
                        return f"@{original_qq_id}"
                return match.group(0)  # 没有找到映射，保持原样
            
            return re.sub(r'<@(\d+)>', replace_discord_at, text)
        else:
            # QQ -> Discord: 将 @QQ用户名 转换为 <@DiscordID>
            def replace_qq_at(match):
                qq_username = match.group(1)
                # 查找QQ用户映射到Discord的ID
                key = f"aiocqhttp:{qq_username}:discord"
                discord_id = user_mapping_manager.mappings.get(key)
                if discord_id:
                    return f"<@{discord_id}>"
                return match.group(0)  # 没有找到映射，保持原样
            
            return re.sub(r'@(\w+)', replace_qq_at, text)
    
    @staticmethod
    def auto_create_mapping_if_needed(user_mapping_manager, source_platform: str, source_user_id: str, target_platform: str, target_user_id: str):
        """自动创建映射（如果不存在）
        
        Args:
            user_mapping_manager: 用户映射管理器实例
            source_platform: 源平台
            source_user_id: 源用户ID
            target_platform: 目标平台
            target_user_id: 目标用户ID
        """
        try:
            # 创建源平台到目标平台的映射
            user_mapping_manager.create_mapping_if_not_exists(source_platform, source_user_id, target_platform, target_user_id)
            
            # 同时创建反向映射（目标平台到源平台）
            user_mapping_manager.create_mapping_if_not_exists(target_platform, target_user_id, source_platform, source_user_id)
            
            logger.info(f"✅ 自动映射创建成功: {source_platform}:{source_user_id} <-> {target_platform}:{target_user_id}")
        except Exception as e:
            logger.error(f"❌ 自动创建映射失败: {e}")


class DiscordWebhookManager:
    """Discord Webhook管理器"""
    
    def __init__(self, context=None):
        self._discord_client = None
        self._context = context
        self.user_mapping = UserMappingManager()
    
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
            logger.error("❌ 未安装discord库，无法自动创建Webhook")
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
            
            # 检查是否可以创建Webhook
            if not hasattr(channel, 'create_webhook'):
                logger.error(f"❌ 频道 {channel_id} 不支持创建Webhook")
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
    def get_qq_avatar_url(original_qq_id: str) -> str:
        """获取QQ用户头像URL"""
        return f"http://q1.qlogo.cn/g?b=qq&nk={original_qq_id}&s=100"
    
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
        """格式化消息内容为文本
        
        Returns:
            str: 文本内容（Discord会自动识别URL并显示图片）
        """
        content_parts = []
        for component in message_chain:
            # 处理文本
            if hasattr(component, 'text') and component.text:
                content_parts.append(component.text)
            # 处理@消息
            elif hasattr(component, 'qq') and component.qq:
                content_parts.append(f"<@{component.qq}>")
            # 处理URL（Discord会自动识别并显示图片）
            elif hasattr(component, 'url') and component.url:
                content_parts.append(component.url)
            # 处理其他可能包含URL的组件
            elif hasattr(component, 'file') and hasattr(component.file, 'url'):
                content_parts.append(component.file.url)
            elif hasattr(component, 'src'):
                content_parts.append(component.src)
        
        return "".join(content_parts)
    
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