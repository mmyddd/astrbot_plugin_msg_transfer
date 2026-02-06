import asyncio
import json
import os
import secrets
from pathlib import Path

import astrbot.api.star as star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

import string

from astrbot.core.message.components import BaseMessageComponent, Plain
from .webhook import DiscordWebhookManager


# ------------------------
# 工具与数据路径
# ------------------------


def load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("❌ 文件不存在！本次创建空 JSON！")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"❌ 文件 {path} 不是有效 JSON: {e}")
        raise ValueError(f"❌ 文件 {path} 不是有效 JSON: {e}") from e
    except OSError as e:
        logger.error(f"❌ 读取文件 {path} 失败: {e}")
        raise RuntimeError(f"❌ 读取文件 {path} 失败: {e}") from e
    except Exception as e:
        logger.error(f"❌ 发生预期外的 JSON 读取错误: {e}！")
        raise RuntimeError(f"❌ 发生预期外的 JSON 读取错误: {e}！")


def save_json(path: Path, data: dict):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError as e:
        logger.error(f"❌ 写入文件 {path} 失败: {e}")
        raise RuntimeError(f"❌ 写入文件 {path} 失败: {e}") from e
    except TypeError as e:
        logger.error(f"❌ 数据无法序列化为 JSON: {e}")
        raise ValueError(f"❌ 数据无法序列化为 JSON: {e}") from e
    except Exception as e:
        logger.error(f"❌ 发生预期外的 JSON 写入错误: {e}")
        raise RuntimeError(f"❌ 发生预期外的 JSON 写入错误: {e}") from e


def gen_code(n=6):
    # 使用 secrets 模块生成更安全的随机字符串
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))


def format_origin_header(event: AstrMessageEvent, umo: str):
    try:
        _, msg_type, conversation_id = umo.split(":", 2)
    except ValueError:
        msg_type = "Unknown"
        conversation_id = "Unknown"

    source_platform = event.get_platform_name()
    sender_name = event.get_sender_name()
    sender_id = event.get_sender_id()

    # 平台友好名称
    source_platform_map = {
        "aiocqhttp": "QQ",
        "discord": "Discord"
    }
    source_platform_human = source_platform_map.get(source_platform, source_platform)

    # 消息类型友好名称
    if msg_type == "GroupMessage":
        msg_type_human = f"群组（ID: {conversation_id}）消息"
    elif msg_type_human == "FriendMessage":
        msg_type_human = f"私聊（对方 ID: {conversation_id}）消息"
    else:
        msg_type_human = f"未知类型（ID: {conversation_id}）消息"

    return (
        f"[转发] {sender_name} ({sender_id})\n"
        f"来自 {source_platform_human} 的 {msg_type_human}"
    )


# ------------------------
# 存储层（无锁简化）
# ------------------------
class MsgTransferStore:
    def __init__(self, rule_file: Path, pending_file: Path, webhook_file: Path, mapping_file: Path):
        self.rule_file = rule_file
        self.pending_file = pending_file
        self.webhook_file = webhook_file
        self.mapping_file = mapping_file
        self._ensure_files()

    def _ensure_files(self):
        if not self.rule_file.exists():
            self.rule_file.write_text("{}", encoding="utf-8")
        if not self.pending_file.exists():
            self.pending_file.write_text("{}", encoding="utf-8")
        if not self.webhook_file.exists():
            self.webhook_file.write_text("{}", encoding="utf-8")
        if not self.mapping_file.exists():
            self.mapping_file.write_text("{}", encoding="utf-8")

    # ----- rules -----
    def load_rules(self):
        return load_json(self.rule_file)

    def save_rules(self, data: dict):
        save_json(self.rule_file, data)

    def add_rule(self, source_umo: str, target_umo: str) -> str:
        data = self.load_rules()

        # 查重
        for rid, rule in data.items():
            if rule["source_umo"] == source_umo and rule["target_umo"] == target_umo:
                raise ValueError(f"规则已存在 #{rid}")

        new_id = str(max(map(int, data.keys()), default=0) + 1)
        data[new_id] = {
            "source_umo": source_umo,
            "target_umo": target_umo
        }
        self.save_rules(data)
        return new_id

    def delete_rule(self, rid: str):
        data = self.load_rules()
        if rid not in data:
            raise KeyError("规则不存在")
        data.pop(rid)
        self.save_rules(data)

    def list_rules(self, source_umo):
        data = self.load_rules()
        
        # 首先尝试精确匹配
        exact_matches = {rid: r for rid, r in data.items() if r["source_umo"] == source_umo}
        if exact_matches:
            return exact_matches
        
        # 如果精确匹配失败，尝试模糊匹配（处理会话隔离关闭的情况）
        # 当会话隔离关闭时，source_umo 格式从 "platform:GroupMessage:group_user" 变成 "platform:GroupMessage:user"
        fuzzy_matches = {}
        
        try:
            parts = source_umo.split(":")
            if len(parts) >= 3:
                platform = parts[0]
                msg_type = parts[1]
                current_id_part = parts[2]  # 可能是用户ID或群组_用户ID
                
                for rid, rule in data.items():
                    rule_source = rule["source_umo"]
                    rule_parts = rule_source.split(":")
                    
                    if len(rule_parts) >= 3:
                        rule_platform = rule_parts[0]
                        rule_msg_type = rule_parts[1]
                        rule_id_part = rule_parts[2]
                        
                        # 检查平台和消息类型是否匹配
                        if rule_platform == platform and rule_msg_type == msg_type:
                            # 检查ID是否匹配（可能是完整匹配或后缀匹配）
                            if (rule_id_part == current_id_part or 
                                rule_id_part.endswith("_" + current_id_part) or
                                current_id_part.endswith("_" + rule_id_part)):
                                fuzzy_matches[rid] = rule
        
        except Exception as e:
            logger.error(f"[FuzzyMatch] 模糊匹配异常: {e}")
        
        return fuzzy_matches

    # ----- pending -----
    def load_pending(self):
        return load_json(self.pending_file)

    def save_pending(self, data: dict):
        save_json(self.pending_file, data)

    def add_pending(self, code: str, source_umo: str):
        p = self.load_pending()
        p[code] = source_umo
        self.save_pending(p)

    def pop_pending(self, code: str):
        p = self.load_pending()
        if code not in p:
            raise KeyError("绑定码不存在或已使用")
        source_umo = p.pop(code)
        self.save_pending(p)
        return source_umo

    # ----- webhook -----
    def load_webhooks(self):
        return load_json(self.webhook_file)

    def save_webhooks(self, data: dict):
        save_json(self.webhook_file, data)

    def set_webhook_url(self, target_umo: str, webhook_url: str):
        data = self.load_webhooks()
        data[target_umo] = webhook_url
        self.save_webhooks(data)

    def get_webhook_url(self, target_umo: str) -> str | None:
        data = self.load_webhooks()
        return data.get(target_umo)

    def remove_webhook_url(self, target_umo: str):
        data = self.load_webhooks()
        if target_umo in data:
            del data[target_umo]
            self.save_webhooks(data)

    # ----- mapping -----
    def load_mappings(self):
        return load_json(self.mapping_file)

    def save_mappings(self, data: dict):
        save_json(self.mapping_file, data)


# ------------------------
# 插件主体
# ------------------------
class MsgTransfer(star.Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 使用 AstrBot 提供的标准方法获取项目持久化数据存储目录
        self.data_dir = star.StarTools.get_data_dir("msg_transfer")
        self.rule_file = self.data_dir / "rules.json"
        self.pending_file = self.data_dir / "pending.json"
        self.webhook_file = self.data_dir / "webhooks.json"
        self.mapping_file = self.data_dir / "mappings.json"

        self.store = MsgTransferStore(self.rule_file, self.pending_file, self.webhook_file, self.mapping_file)
        self.webhook_manager = DiscordWebhookManager(context)

    async def initialize(self):
        logger.info("MsgTransfer plugin init OK")

    @filter.command_group("mt")
    def mt(self):
        """mt 命令组"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mt.command("add")
    async def cmd_add(self, event: AstrMessageEvent):
        """创建一则消息转发绑定的请求"""
        code = gen_code()
        source_umo = str(event.unified_msg_origin)
        self.store.add_pending(code, source_umo)

        yield event.plain_result(
            f"📌 已创建绑定请求\n"
            f"请在目标会话执行：#mt bind {code}"
        )

    @mt.command("bind")
    async def cmd_bind(self, event: AstrMessageEvent, code: str):
        """接受一则消息转发绑定的请求"""
        try:
            target_umo = str(event.unified_msg_origin)
            source_umo = self.store.pop_pending(code)
            rid = self.store.add_rule(source_umo, target_umo)
            
            # 如果目标是Discord，自动创建Webhook（黑盒操作，不告知用户）
            # 检查平台名称或UMO格式
            target_platform = event.get_platform_name()
            is_discord = target_platform == "discord" or "discord" in target_umo.lower()
            
            if is_discord:
                # 提取频道ID
                channel_id = None
                parts = target_umo.split(":")
                if len(parts) >= 3:
                    channel_id = parts[2]
                elif len(parts) == 2:
                    channel_id = parts[1]
                
                if channel_id:
                    webhook_url = await self.webhook_manager.create_webhook_for_channel(int(channel_id))
                    if webhook_url:
                        self.store.set_webhook_url(target_umo, webhook_url)
            
            yield event.plain_result(f"✅ 绑定成功 # {rid}")
        except Exception as e:
            logger.error(f"[Bind] 绑定异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 绑定失败：{e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mt.command("del")
    async def cmd_del(self, event: AstrMessageEvent, rid: str):
        """删除一条转发规则"""
        try:
            self.store.delete_rule(rid)
            yield event.plain_result(f"🗑️ 已删除规则 #{rid}")
        except Exception as e:
            yield event.plain_result(f"❌ 删除失败: {e}")

    @mt.command("list")
    async def cmd_list(self, event: AstrMessageEvent):
        """列出与当前会话相关的所有转发规则"""
        source_umo = str(event.unified_msg_origin)
        rules = self.store.list_rules(source_umo)
        if not rules:
            yield event.plain_result("📭 当前没有转发规则")
            return

        lines = [f"📜 转发规则（{len(rules)}条）"]
        for rid, r in rules.items():
            lines.append(f"#{rid}")
        yield event.plain_result("\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def forward_message(self, event: AstrMessageEvent):
        """主转发逻辑 - 顺序队列处理所有转发规则，保证顺序一致"""
        try:
            source_umo = str(event.unified_msg_origin)
            rules = self.store.list_rules(source_umo)
            if not rules:
                return
            message_chain = event.get_messages()
            # 顺序依次await每个转发，保证顺序
            for rid, rule in rules.items():
                await self._forward_single_rule(event, rule, rid, source_umo, message_chain)
        except Exception as e:
            logger.error(f"❌ 转发逻辑异常: {e}", exc_info=True)

    async def _forward_single_rule(self, event: AstrMessageEvent, rule: dict, rid: str, source_umo: str, message_chain):
        """处理单个转发规则"""
        try:
            # 自动记录QQ号和名称到 mapping_file
            platform = event.get_platform_name()
            if platform in ["aiocqhttp", "qqofficial"]:
                qq_id = event.get_sender_id()
                qq_name = event.get_sender_name()
                # 读取现有映射
                mapping = self.store.load_mappings()
                if mapping.get(qq_id) != qq_name:
                    mapping[qq_id] = qq_name
                    self.store.save_mappings(mapping)
                    logger.info(f"转发时已更新QQ号 {qq_id} 的名称: {mapping.get(qq_id)} -> {qq_name}")

            target = rule["target_umo"]
            webhook_url = self.store.get_webhook_url(target)
            if webhook_url:
                success = await self._forward_with_webhook(event, target, message_chain, rid, webhook_url)
                if success:
                    return
            try:
                header = format_origin_header(event, source_umo)
                header += "\n\n\u200b"
                new_chain = list[BaseMessageComponent]([Plain(text=header)]) + message_chain
                await self.context.send_message(target, event.chain_result(new_chain))
            except ValueError as e:
                logger.error(f"❌ 不合法的 session 字符串，转发失败 #{rid}: {e}")
            except Exception as e:
                logger.error(f"❌ 转发失败 #{rid}: {e}")
        except Exception as e:
            logger.error(f"❌ 处理规则 #{rid} 时发生异常: {e}")
    
    async def _forward_with_webhook(self, event: AstrMessageEvent, target_umo: str, message_chain, rule_id: str, webhook_url: str) -> bool:
        try:
            sender_name = event.get_sender_name()
            sender_id = event.get_sender_id()
            source_platform = event.get_platform_name()

            # 加载QQ号-名称映射
            mapping = self.store.load_mappings()

            # 检查是否有引用消息（Quote/Reply）
            quote_text = None
            quote_sender = None
            # 适配 OneBot v11 的引用消息段类型为 'Quote' 或 'Reply'
            for seg in message_chain:
                if seg.__class__.__name__ in ("Quote", "Reply"):
                    # 尝试获取被引用消息内容和发送者
                    if hasattr(seg, "origin_text"):
                        quote_text = seg.origin_text
                    if hasattr(seg, "origin_sender"):
                        quote_sender = seg.origin_sender
                    # 兼容部分平台字段
                    if hasattr(seg, "text") and not quote_text:
                        quote_text = seg.text
                    if hasattr(seg, "sender_name") and not quote_sender:
                        quote_sender = seg.sender_name
                    break

            # 替换消息链中的At(QQ)为对应名称
            new_chain = []
            for seg in message_chain:
                if seg.__class__.__name__ == "At" and hasattr(seg, "qq"):
                    qq_id = str(seg.qq)
                    qq_name = mapping.get(qq_id, qq_id)
                    new_chain.append(Plain(f"@{qq_name} "))
                elif seg.__class__.__name__ in ("Quote", "Reply"):
                    continue  # 不直接转发引用段
                else:
                    new_chain.append(seg)

            # 构建引用文本（如有）
            quote_block = None
            if quote_text:
                # 检查quote_text是否为图片链接，如果是则单独一行，否则正常引用
                if (quote_text.startswith('http://') or quote_text.startswith('https://')) and (quote_text.endswith('.jpg') or quote_text.endswith('.png') or quote_text.endswith('.jpeg') or quote_text.endswith('.gif') or quote_text.endswith('.webp')):
                    quote_block = f"> [图片]({quote_text})\n"
                else:
                    quote_block = f"> {quote_text}\n"

            # 构建虚拟用户信息
            virtual_username = DiscordWebhookManager.build_virtual_username(sender_name, source_platform)
            avatar_url = DiscordWebhookManager.get_avatar_url(source_platform, sender_id)
            content = DiscordWebhookManager.format_message_content_async(new_chain)
            if quote_block:
                content = quote_block + content

            success = await DiscordWebhookManager.send_webhook_message(
                webhook_url=webhook_url,
                username=virtual_username,
                avatar_url=avatar_url,
                content=content
            )
            return success
        except Exception as e:
            logger.error(f"❌ Webhook转发异常 #{rule_id}: {e}")
            return False

    async def terminate(self):
        logger.info("MsgTransfer plugin terminated")