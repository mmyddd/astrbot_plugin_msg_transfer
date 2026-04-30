# MsgTransfer —— AstrBot 跨平台消息转发插件

> **本仓库基于 [Siaospeed/astrbot_plugin_msg_transfer](https://github.com/Siaospeed/astrbot_plugin_msg_transfer) 进行 fork 改动，遵守 AGPL-3.0 许可证。**

一个用于在 **QQ** 与 **Discord** 之间双向转发与同步消息的 AstrBot 插件，支持回复引用链还原、原生 @提及、图片转发等特性。

---

## ✨ 特性

- **QQ ↔ Discord 双向转发**：消息在两端之间自动同步。
- **消息来源标注**：自动在消息前标记发送者及来源平台（`[转发] 发送者名 (平台): 内容`）。
- **回复引用链还原**：
  - QQ 端回复已转发的消息时，自动引用原始 QQ 消息并 @ 原始发送者。
  - Discord 端引用回复时，自动还原为 Discord 原生引用 + 跳转链接。
- **Discord Webhook 集成**：自动为 Discord 频道创建 Webhook，以发送者身份显示头像和昵称。
- **原生 Discord @提及**：QQ → Discord 转发时，QQ @提及自动转换为 Discord 原生 `<@user_id>` 格式。
- **可持久化存储**：转发规则、消息映射缓存自动保存，重启不丢失。

---

## 🚀 快速开始

1. **下载插件**：从本仓库的 Release 下载 `.zip` 文件，在 AstrBot WebUI 的插件页面中选择「从文件安装」。
2. **安装依赖**：重启 AstrBot 会自动安装依赖，也可手动执行：
    ```bash
    pip install -r requirements.txt
    ```
3. **（可选）Discord 原生 @提及**：如需 QQ→Discord 转发的 @提及功能，还需安装 `discord.py`：
    ```bash
    pip install discord.py>=2.0.0
    ```
4. **重启 AstrBot**。

---

## ⚙️ 配置

插件无需额外配置，所有规则通过命令创建。绑定 Discord 目标时会自动创建 Webhook。

---

## 💬 指令列表

| 指令 | 说明 | 权限 |
|------|------|------|
| `mt add` | 在当前会话创建转发绑定请求 | ADMIN |
| `mt bind <code>` | 在目标会话接受绑定请求 | 任意 |
| `mt del <rid>` | 删除指定转发规则 | ADMIN |
| `mt list` | 列出当前会话相关的所有转发规则 | 任意 |

---

## 📦 数据存储

插件在 `data/plugin_data/astrbot_plugin_DiscordToQQTransfer/` 下维护以下文件：

| 文件 | 用途 |
|------|------|
| `rules.json` | 转发规则（源 UMO → 目标 UMO） |
| `pending.json` | 待绑定的请求 |
| `webhooks.json` | Discord Webhook URL 映射 |
| `mappings.json` | QQ 号 → QQ 昵称映射 |
| `msg_mapping.json` | QQ 消息 ID ↔ Discord 消息 ID 映射（含发送者信息） |
| `forward_log.json` | Discord 转发消息记录（用于多跳引用链还原） |

---

## 🔄 转发行为示例

假设 QQ 群 `654321` 与 Discord 频道 `123456` 已绑定：

### QQ → Discord

```
QQ: mmyddd: 1
→ Discord (Webhook): mmyddd (QQ): 1
```

### Discord → QQ

```
Discord: mmyddd: 2
→ QQ: [转发] mmyddd (discord): 2
```

### 多跳引用链（QQ 回复 → Discord 引用回复 → ...）

```
① QQ: mmyddd: 1
② Discord (Webhook): mmyddd (QQ):  1
③ Discord: mmyddd (引用②): 2
④ QQ (引用①, @mmyddd): [转发] mmyddd (discord): 2
⑤ QQ: mmyddd (引用④): 3
⑥ Discord (引用③, @mmyddd): mmyddd (QQ): 3
```

---

## 🧩 项目结构

```
astrbot_plugin_msg_transfer/
├── LICENSE
├── README.md
├── main.py          # 插件主逻辑
├── webhook.py       # Discord Webhook 管理模块
├── metadata.yaml
└── requirements.txt
```

---

## 📜 许可证

- 本插件以 **AGPL-3.0** 开源。
- 基于 [Siaospeed/astrbot_plugin_msg_transfer](https://github.com/Siaospeed/astrbot_plugin_msg_transfer) 进行 fork 改动。
- 上游项目与 AstrBot 框架均基于 AGPL-3.0，因此本插件同样以 AGPL-3.0 分发。

---

## 🤝 贡献

欢迎提交 Issue 或 PR。
