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
    elif msg_type == "FriendMessage":
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
    def __init__(self, rule_file: Path, pending_file: Path, webhook_file: Path):
        self.rule_file = rule_file
        self.pending_file = pending_file
        self.webhook_file = webhook_file
        self._ensure_files()

    def _ensure_files(self):
        if not self.rule_file.exists():
            self.rule_file.write_text("{}", encoding="utf-8")
        if not self.pending_file.exists():
            self.pending_file.write_text("{}", encoding="utf-8")
        if not self.webhook_file.exists():
            self.webhook_file.write_text("{}", encoding="utf-8")

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
        return {rid: r for rid, r in data.items() if r["source_umo"] == source_umo}

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

        self.store = MsgTransferStore(self.rule_file, self.pending_file, self.webhook_file)
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
            
            # 如果目标是Discord，自动创建Webhook
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
                        yield event.plain_result(
                            f"✅ 已绑定 #{rid}\n"
                            f"{source_umo} → {target_umo}\n"
                            f"🎉 已自动创建Webhook，消息将以虚拟用户形式显示"
                        )
                    else:
                        yield event.plain_result(
                            f"✅ 已绑定 #{rid}\n"
                            f"{source_umo} → {target_umo}\n"
                            f"⚠️ 自动创建Webhook失败，将使用普通转发模式"
                        )
                else:
                    yield event.plain_result(f"✅ 已绑定 #{rid}\n{source_umo} → {target_umo}")
            else:
                yield event.plain_result(f"✅ 已绑定 #{rid}\n{source_umo} → {target_umo}")
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
            yield event.plain_result("📭 当前会话没有规则")
            return

        lines = [f"📜 当前会话({source_umo}) 的规则："]
        for rid, r in rules.items():
            webhook_status = "✨ 虚拟用户" if self.store.get_webhook_url(r['target_umo']) else "📤 普通转发"
            lines.append(f"#{rid} {r['source_umo']} → {r['target_umo']} [{webhook_status}]")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mt.command("webhook")
    async def cmd_webhook(self, event: AstrMessageEvent, action: str = ""):
        """管理Discord Webhook
        
        用法：
        /mt webhook           - 查看当前Webhook状态
        /mt webhook create    - 自动创建Webhook
        /mt webhook <URL>     - 手动设置Webhook URL
        """
        current_umo = str(event.unified_msg_origin)
        
        # 提取channel_id（支持多种UMO格式）
        channel_id = None
        parts = current_umo.split(":")
        
        # 尝试获取频道ID
        if len(parts) >= 3:
            channel_id = parts[2]
        elif len(parts) == 2:
            channel_id = parts[1]
        
        if not channel_id:
            yield event.plain_result(f"❌ 无法获取频道ID，UMO格式: {current_umo}")
            return
        
        if not action:
            # 查看当前Webhook状态
            existing_url = self.store.get_webhook_url(current_umo)
            if existing_url:
                yield event.plain_result(
                    f"✨ 当前Webhook已启用\n"
                    f"URL: {existing_url}\n"
                    f"💡 转发的消息将以虚拟用户形式显示"
                )
            else:
                yield event.plain_result(
                    f"📭 当前会话未设置Webhook\n"
                    f"💡 提示：执行 `/mt webhook create` 自动创建Webhook"
                )
        elif action.lower() == "create":
            # 自动创建Webhook
            yield event.plain_result("🔄 正在自动创建Webhook...")
            webhook_url = await self.webhook_manager.create_webhook_for_channel(int(channel_id))
            if webhook_url:
                self.store.set_webhook_url(current_umo, webhook_url)
                yield event.plain_result(
                    f"✅ Webhook创建成功！\n"
                    f"💡 转发的消息将以虚拟用户形式显示，\n"
                    f"   显示原始发送者的头像和名字"
                )
            else:
                yield event.plain_result(
                    "❌ 自动创建Webhook失败\n"
                    "💡 可能原因：\n"
                    "   1. 机器人没有创建Webhook的权限\n"
                    "   2. 未安装discord库\n"
                    "   3. 请联系管理员手动创建Webhook并设置"
                )
        else:
            # 手动设置Webhook URL
            self.store.set_webhook_url(current_umo, action)
            yield event.plain_result(
                "✅ 已手动设置Webhook URL\n"
                "💡 转发的消息将以虚拟用户形式显示"
            )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def forward_message(self, event: AstrMessageEvent):
        """主转发逻辑 - 并行处理所有转发规则"""
        try:
            source_umo = str(event.unified_msg_origin)
            
            rules = self.store.list_rules(source_umo)
            
            if not rules:
                return

            message_chain = event.get_messages()

            # 并行处理所有转发规则
            tasks = []
            for rid, rule in rules.items():
                task = self._forward_single_rule(event, rule, rid, source_umo, message_chain)
                tasks.append(task)
            
            # 使用gather并行执行所有转发任务
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"❌ 转发逻辑异常: {e}", exc_info=True)

    async def _forward_single_rule(self, event: AstrMessageEvent, rule: dict, rid: str, source_umo: str, message_chain):
        """处理单个转发规则"""
        try:
            target = rule["target_umo"]
            
            # 尝试使用Webhook转发（如果有配置）
            webhook_url = self.store.get_webhook_url(target)
            
            if webhook_url:
                # 使用Webhook转发
                success = await self._forward_with_webhook(event, target, message_chain, rid, webhook_url)
                if success:
                    return  # Webhook成功，跳过普通转发
            
            # 普通转发（如果没有Webhook或Webhook失败）
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
        """使用Webhook转发到Discord，创建虚拟用户"""
        try:
            # 获取发送者信息
            sender_name = event.get_sender_name()
            sender_id = event.get_sender_id()
            source_platform = event.get_platform_name()
            
            # 构建虚拟用户信息
            virtual_username = DiscordWebhookManager.build_virtual_username(sender_name, source_platform)
            avatar_url = DiscordWebhookManager.get_avatar_url(source_platform, sender_id)
            
            # 格式化消息内容
            content = DiscordWebhookManager.format_message_content(message_chain)
            
            # 发送Webhook消息
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
