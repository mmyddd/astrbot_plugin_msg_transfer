import json
import secrets
import string
from pathlib import Path

import astrbot.api.message_components as Comp
import astrbot.api.star as star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


# ------------------------
# 工具函数
# ------------------------

def load_json(path: Path) -> dict:
    """加载JSON文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取文件失败 {path}: {e}")
        return {}


def save_json(path: Path, data: dict):
    """保存JSON文件"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入文件失败 {path}: {e}")


def gen_code(n=6):
    """生成绑定码"""
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))


def format_origin_header(event: AstrMessageEvent, umo: str):
    """格式化转发消息头部（仅支持Discord和QQ）"""
    try:
        _, msg_type, conversation_id = umo.split(":", 2)
    except ValueError:
        msg_type = "Unknown"
        conversation_id = "Unknown"

    source_platform = event.get_platform_name()
    sender_name = event.get_sender_name()
    sender_id = event.get_sender_id()

    # 仅支持Discord和QQ
    source_platform_map = {
        "aiocqhttp": "QQ",
        "discord": "Discord",
    }
    source_platform_human = source_platform_map.get(source_platform, source_platform)

    # 消息类型
    if msg_type == "GroupMessage":
        msg_type_human = f"群组（{conversation_id}）"
    elif msg_type == "FriendMessage":
        msg_type_human = f"私聊（{conversation_id}）"
    else:
        msg_type_human = f"未知类型（{conversation_id}）"

    return f"[转发] {sender_name}({sender_id}) - 来自{source_platform_human}的{msg_type_human}\n"


# ------------------------
# 存储层
# ------------------------
class MsgTransferStore:
    def __init__(self, rule_file: Path, pending_file: Path):
        self.rule_file = rule_file
        self.pending_file = pending_file
        self._ensure_files()

    def _ensure_files(self):
        if not self.rule_file.exists():
            self.rule_file.write_text("{}", encoding="utf-8")
        if not self.pending_file.exists():
            self.pending_file.write_text("{}", encoding="utf-8")

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


# ------------------------
# 插件主体
# ------------------------
@register(
    "astrbot_plugin_dis2qq_transfer",
    "mmyddd",
    "Discord和QQ之间消息转发插件",
    "0.1.0",
    "https://github.com/mmyddd/astrbot_plugin_msg_transfer"
)
class MsgTransfer(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = star.StarTools.get_data_dir("msg_transfer")
        self.rule_file = self.data_dir / "rules.json"
        self.pending_file = self.data_dir / "pending.json"
        self.store = MsgTransferStore(self.rule_file, self.pending_file)

    async def initialize(self):
        logger.info("MsgTransfer plugin init OK")

    @filter.command_group("mt")
    def mt(self):
        """mt 命令组"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mt.command("add")
    async def cmd_add(self, event: AstrMessageEvent):
        """创建转发绑定请求"""
        # 检查平台是否支持
        if event.get_platform_name() not in ["aiocqhttp", "discord"]:
            yield event.plain_result("❌ 仅支持QQ和Discord平台")
            return

        code = gen_code()
        source_umo = str(event.unified_msg_origin)
        self.store.add_pending(code, source_umo)

        yield event.plain_result(
            f"📌 已创建绑定请求\n"
            f"请在目标会话执行：#mt bind {code}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mt.command("bind")
    async def cmd_bind(self, event: AstrMessageEvent, code: str):
        """接受转发绑定请求"""
        # 检查平台是否支持
        if event.get_platform_name() not in ["aiocqhttp", "discord"]:
            yield event.plain_result("❌ 仅支持QQ和Discord平台")
            return

        try:
            target_umo = str(event.unified_msg_origin)
            source_umo = self.store.pop_pending(code)
            
            # 验证源平台
            source_platform = source_umo.split(":")[0]
            if source_platform not in ["aiocqhttp", "discord"]:
                yield event.plain_result("❌ 源会话平台不支持，仅支持QQ和Discord")
                return
            
            rid = self.store.add_rule(source_umo, target_umo)
            yield event.plain_result(f"✅ 已绑定 #{rid}\n{source_umo} → {target_umo}")
        except Exception as e:
            yield event.plain_result(f"❌ 绑定失败：{e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mt.command("del")
    async def cmd_del(self, event: AstrMessageEvent, rid: str):
        """删除转发规则"""
        try:
            self.store.delete_rule(rid)
            yield event.plain_result(f"🗑️ 已删除规则 #{rid}")
        except Exception as e:
            yield event.plain_result(f"❌ 删除失败: {e}")

    @mt.command("list")
    async def cmd_list(self, event: AstrMessageEvent):
        """列出当前会话的转发规则"""
        source_umo = str(event.unified_msg_origin)
        rules = self.store.list_rules(source_umo)
        
        if not rules:
            yield event.plain_result("📭 当前会话没有规则")
            return

        lines = [f"📜 当前会话规则："]
        for rid, r in rules.items():
            lines.append(f"#{rid} {r['source_umo']} → {r['target_umo']}")
        yield event.plain_result("\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP | filter.PlatformAdapterType.DISCORD)
    async def forward_message(self, event: AstrMessageEvent):
        """主转发逻辑"""
        try:
            source_umo = str(event.unified_msg_origin)
            rules = self.store.list_rules(source_umo)
            
            if not rules:
                return

            # 获取消息链
            message_chain = event.get_messages()

            for rid, rule in rules.items():
                target = rule["target_umo"]
                try:
                    header = format_origin_header(event, source_umo)
                    # 构建新的消息链，添加转发头
                    new_chain = [Comp.Plain(text=header)] + list(message_chain)
                    # 发送消息到目标会话
                    await self.context.send_message(target, new_chain)
                except Exception as e:
                    logger.error(f"转发失败 #{rid}: {e}")

        except Exception as e:
            logger.error(f"转发逻辑异常: {e}")

    async def terminate(self):
        logger.info("MsgTransfer plugin terminated")