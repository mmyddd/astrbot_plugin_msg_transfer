import json
import re
import secrets
from pathlib import Path

import astrbot.api.star as star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

import string

from astrbot.core.message.components import Plain
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
        logger.error(f"❌ 发生预期外的 JSON 读取错误: {e}", exc_info=True)
        raise RuntimeError(f"❌ 发生预期外的 JSON 读取错误: {e}")


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


# ------------------------
# 存储层（无锁简化）
# ------------------------
class MsgTransferStore:
    def __init__(self, rule_file: Path, pending_file: Path, webhook_file: Path, mapping_file: Path, msg_mapping_file: Path, forward_log_file: Path):
        self.rule_file = rule_file
        self.pending_file = pending_file
        self.webhook_file = webhook_file
        self.mapping_file = mapping_file
        self.msg_mapping_file = msg_mapping_file
        self.forward_log_file = forward_log_file
        self._ensure_files()

    def _ensure_files(self):
        self.rule_file.parent.mkdir(parents=True, exist_ok=True)
        for f in (self.rule_file, self.pending_file, self.webhook_file, self.mapping_file, self.msg_mapping_file, self.forward_log_file):
            if not f.exists():
                f.write_text("{}", encoding="utf-8")

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

    # ----- msg_id mapping (QQ msg_id -> Discord msg_id) -----
    MAX_MSG_MAPPINGS = 2000

    def load_msg_mapping(self):
        return load_json(self.msg_mapping_file)

    def save_msg_mapping(self, data: dict):
        save_json(self.msg_mapping_file, data)

    def set_msg_mapping(self, qq_msg_id: str, discord_msg_id: str):
        data = self.load_msg_mapping()
        data[qq_msg_id] = discord_msg_id
        # 限制条目数，防止无限增长
        if len(data) > self.MAX_MSG_MAPPINGS:
            keys = list(data.keys())[:len(data) // 2]
            for k in keys:
                del data[k]
        self.save_msg_mapping(data)

    def get_msg_mapping(self, qq_msg_id: str) -> str | None:
        data = self.load_msg_mapping()
        return data.get(qq_msg_id)

    # ----- forward_log (Discord→QQ 消息记录，用于回复引用链) -----
    MAX_FORWARD_LOG = 200

    def load_forward_log(self):
        return load_json(self.forward_log_file)

    def save_forward_log(self, data: dict):
        save_json(self.forward_log_file, data)

    def add_forward_log(self, discord_msg_id: str, content: str, sender_id: str = ""):
        """记录一条从 Discord 转发到 QQ 的消息"""
        data = self.load_forward_log()
        data[discord_msg_id] = {
            "content": content,
            "sender_id": sender_id,
            "timestamp": __import__("time").time()
        }
        if len(data) > self.MAX_FORWARD_LOG:
            sorted_keys = sorted(data, key=lambda k: data[k]["timestamp"])
            for k in sorted_keys[:len(data) // 2]:
                del data[k]
        self.save_forward_log(data)

    def find_forward_log_by_content(self, content: str) -> str | None:
        """通过消息内容查找最近转发的 Discord 消息 ID"""
        if not content:
            return None
        data = self.load_forward_log()
        best = None
        best_time = 0
        for d_msg_id, entry in data.items():
            if entry.get("content") == content and entry.get("timestamp", 0) > best_time:
                best = d_msg_id
                best_time = entry["timestamp"]
        return best

    def find_forward_log_sender(self, content: str) -> str | None:
        """通过消息内容查找转发消息的 Discord 发送者 ID"""
        if not content:
            return None
        data = self.load_forward_log()
        for d_msg_id, entry in data.items():
            if entry.get("content") == content:
                return entry.get("sender_id")
        return None


# ------------------------
# 插件主体
# ------------------------
class MsgTransfer(star.Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 使用 AstrBot 提供的标准方法获取项目持久化数据存储目录
        self.data_dir = star.StarTools.get_data_dir("msg_transfer")
        self.rule_file = self.data_dir / "rules.json"
        self.forward_log_file = self.data_dir / "forward_log.json"
        self.pending_file = self.data_dir / "pending.json"
        self.webhook_file = self.data_dir / "webhooks.json"
        self.mapping_file = self.data_dir / "mappings.json"
        self.msg_mapping_file = self.data_dir / "msg_mapping.json"

        self.store = MsgTransferStore(self.rule_file, self.pending_file, self.webhook_file, self.mapping_file, self.msg_mapping_file, self.forward_log_file)
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
            # 记录从 Discord 转发的消息，供 QQ 回复引用时还原跳转链接
            platform = event.get_platform_name()
            if platform == "discord":
                import json as _json
                try:
                    _raw = getattr(event.message_obj, 'raw_message', None)
                    _dict = getattr(event.message_obj, '__dict__', {})
                    _safe_keys = {k: str(v)[:200] for k, v in _dict.items() if not k.startswith('_')}
                    logger.info(f"[DiscordDebug] message_obj keys: {_json.dumps(_safe_keys, ensure_ascii=False)}")
                    # 检查 raw_message 的 reference 相关属性（Discord.py 回复消息）
                    if _raw:
                        _ref = getattr(_raw, 'reference', None)
                        _ref_msg = getattr(_raw, 'referenced_message', None)
                        logger.info(f"[DiscordDebug] reference={_ref}, ref_type={type(_ref).__name__}")
                        logger.info(f"[DiscordDebug] referenced_message={_ref_msg}")
                        if _ref:
                            _ref_mid = _ref.message_id
                            _ref_cid = _ref.channel_id
                            _ref_gid = _ref.guild_id
                            _ref_resolved = _ref.resolved
                            logger.info(f"[DiscordDebug] ref.message_id={_ref_mid}, ref.channel_id={_ref_cid}, ref.guild_id={_ref_gid}")
                            logger.info(f"[DiscordDebug] ref.resolved={_ref_resolved}")
                            if _ref_resolved:
                                logger.info(f"[DiscordDebug] resolved.content={_ref_resolved.content}")
                except Exception as _e:
                    logger.info(f"[DiscordDebug] error inspecting message_obj: {_e}")
                discord_msg_id = event.message_obj.message_id
                if discord_msg_id:
                    msg_text = DiscordWebhookManager.format_message_content(message_chain)
                    if msg_text:
                        self.store.add_forward_log(str(discord_msg_id), msg_text, event.get_sender_id())
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
                await self._forward_with_webhook(event, target, message_chain, rid, webhook_url)
                return

            # 非 webhook 目标（如 QQ），通过 AstrBot 框架发送
            try:
                from astrbot.core.message.message_event_result import MessageChain
                from astrbot.core.message.components import Plain as APlain
                sender_name = event.get_sender_name()
                source_platform_name = event.get_platform_name()
                # 将整个消息合并为一行，确保 QQ 回复引用能抓到完整内容
                msg_text = DiscordWebhookManager.format_message_content(message_chain)
                if msg_text:
                    full_text = f"[转发] {sender_name} ({source_platform_name}): {msg_text}"
                else:
                    full_text = f"[转发] {sender_name} ({source_platform_name})"
                chain = MessageChain()
                chain.chain = [APlain(full_text)]
                sent = await self.context.send_message(target, chain)
                if sent:
                    logger.info(f"已转发 #{rid} -> {target}")
                else:
                    logger.warning(f"转发 #{rid} 未找到目标平台适配器: {target}")
            except Exception as e:
                logger.error(f"通过 AstrBot 转发 #{rid} 失败: {e}")
        except Exception as e:
            logger.error(f"❌ 处理规则 #{rid} 时发生异常: {e}")
    
    async def _forward_with_webhook(self, event: AstrMessageEvent, target_umo: str, message_chain, rule_id: str, webhook_url: str) -> bool:
        try:
            sender_name = event.get_sender_name()
            sender_id = event.get_sender_id()
            source_platform = event.get_platform_name()

            mapping = self.store.load_mappings()

            # 检查是否有引用消息（Quote/Reply）
            quote_text = None
            quote_sender = None
            reply_to_qq_id = None  # 被回复的原始QQ消息ID
            for seg in message_chain:
                if seg.__class__.__name__ in ("Quote", "Reply"):
                    if hasattr(seg, "origin_text"):
                        quote_text = seg.origin_text
                    if hasattr(seg, "origin_sender"):
                        quote_sender = seg.origin_sender
                    if hasattr(seg, "text") and not quote_text:
                        quote_text = seg.text
                    if hasattr(seg, "sender_name") and not quote_sender:
                        quote_sender = seg.sender_name
                    if hasattr(seg, "sender_nickname") and not quote_sender:
                        quote_sender = seg.sender_nickname
                    if not quote_text and hasattr(seg, "message_str") and seg.message_str:
                        quote_text = seg.message_str
                    if not quote_text and hasattr(seg, "chain") and seg.chain:
                        for sub in seg.chain:
                            if sub.__class__.__name__ == "File":
                                fname = getattr(sub, "name", None) or getattr(sub, "filename", None) or "文件"
                                quote_text = f"[{fname}]"
                                break
                    if hasattr(seg, "id") and seg.id:
                        reply_to_qq_id = str(seg.id)
                    break

            discord_sender_name = None
            discord_sender_id = None
            # 如果引用文本是 AstrBot 自动转发的格式（[转发]），解析原始发送者和消息
            if quote_text and quote_text.strip().startswith('[转发]'):
                fwd_match = re.match(
                    r"^\[转发\]\s+(.+?)(?:\s+\(\d+\))?\s*[：:]\s*(.*)",
                    quote_text.strip()
                )
                if fwd_match:
                    parsed_sender = fwd_match.group(1).strip()
                    parsed_text = fwd_match.group(2).strip()
                    if parsed_sender:
                        quote_sender = parsed_sender
                    if parsed_text:
                        quote_text = parsed_text
            # 查找之前转发该 QQ 消息时产生的 Discord 消息 ID
            reply_to_discord_id = None
            if reply_to_qq_id:
                reply_to_discord_id = self.store.get_msg_mapping(reply_to_qq_id)

            # 如果 msg_mapping 中找不到（D→Q 方向），尝试从 forward_log 匹配
            if reply_to_discord_id is None and quote_text:
                fwd_discord_id = self.store.find_forward_log_by_content(quote_text)
                if fwd_discord_id:
                    reply_to_discord_id = fwd_discord_id
                    discord_sender_id = self.store.find_forward_log_sender(quote_text)

            # 替换消息链中的At(QQ)为对应名称
            new_chain = []
            for seg in message_chain:
                if seg.__class__.__name__ == "At" and hasattr(seg, "qq"):
                    qq_id = str(seg.qq)
                    # 如果 At 的是机器人自身且有解析出的 Discord 发送者，用 Discord 原生 @mention 或名称
                    self_id = event.get_self_id()
                    if self_id and qq_id == self_id:
                        if discord_sender_id:
                            new_chain.append(Plain(f"<@{discord_sender_id}> "))
                        elif discord_sender_name:
                            new_chain.append(Plain(f"@{discord_sender_name} "))
                    else:
                        qq_name = mapping.get(qq_id, qq_id)
                        new_chain.append(Plain(f"@{qq_name} "))
                elif seg.__class__.__name__ in ("Quote", "Reply"):
                    continue
                else:
                    new_chain.append(seg)

            virtual_username = DiscordWebhookManager.build_virtual_username(sender_name, source_platform)
            avatar_url = DiscordWebhookManager.get_avatar_url(source_platform, sender_id)
            content = DiscordWebhookManager.format_message_content(new_chain)

            # 有对应的 Discord 消息 → 在 webhook 内容中插入跳转链接
            if reply_to_discord_id:
                channel_id = None
                parts = target_umo.split(":")
                if len(parts) >= 3:
                    try:
                        channel_id = int(parts[2])
                    except (ValueError, TypeError):
                        channel_id = None

                jump_url = None
                if channel_id:
                    try:
                        client = self.webhook_manager._get_discord_client()
                        if client:
                            channel = await client.fetch_channel(channel_id)
                            if hasattr(channel, 'guild') and channel.guild:
                                guild_id = channel.guild.id
                                jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{reply_to_discord_id}"
                    except Exception:
                        pass

                prefix = f"**{quote_sender}**: " if quote_sender else ""
                if jump_url:
                    label = quote_text or "引用消息"
                    content = f"> {prefix}[{label}]({jump_url})\n{content}"
                elif quote_text:
                    content = f"> {prefix}{quote_text}\n{content}"

            # 无原生回复时带 markdown 引用
            elif quote_text:
                prefix = f"**{quote_sender}**: " if quote_sender else ""
                if (quote_text.startswith('http://') or quote_text.startswith('https://')) and (quote_text.endswith('.jpg') or quote_text.endswith('.png') or quote_text.endswith('.jpeg') or quote_text.endswith('.gif') or quote_text.endswith('.webp')):
                    quote_block = f"> {prefix}[图片]({quote_text})\n"
                else:
                    quote_block = f"> {prefix}{quote_text}\n"
                content = quote_block + content

            discord_msg_id = await self.webhook_manager.send_webhook_message(
                webhook_url=webhook_url,
                username=virtual_username,
                avatar_url=avatar_url,
                content=content,
            )

            if discord_msg_id:
                qq_msg_id = event.message_obj.message_id
                if qq_msg_id:
                    self.store.set_msg_mapping(qq_msg_id, discord_msg_id)
                return True

            return False
        except Exception as e:
            logger.error(f"❌ Webhook转发异常 #{rule_id}: {e}")
            return False

    async def terminate(self):
        await self.webhook_manager.close()
        logger.info("MsgTransfer plugin terminated")