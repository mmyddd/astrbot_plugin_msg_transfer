# MsgTransfer —— AstrBot 跨平台消息转发插件

一个 **简洁、稳健、可扩展** 的 AstrBot 跨平台消息转发插件，用于在不同聊天平台之间同步消息、桥接群聊，让多个平台之间能够像“同一个群”一样互动。

本插件理论上适用于任意 AstrBot 支持的平台（如 QQ、微信、Telegram、Discord 等）。

> **许可证：AGPL-3.0**  
> 本插件在 AstrBot（AGPL）基础上开发，因此以 AGPL 开源。

---

## ✨ 特性

- **多平台互通**：支持 AstrBot 的任意平台适配器。
- **消息来源标注**：支持自动在消息前增加来源信息（例如 UMO / 平台名 / 发送者）。
- **可持久化存储**：自动保存转发规则，不会因重启丢失。
- **可拓展性高**：所有逻辑模块化，便于二次开发。

---

## 🚀 快速开始
1. **下载插件**：通过 AstrBot 的插件市场直接下载，或从本仓库的 Release 下载 `astrbot_plugin_msg_transfer` 的 `.zip` 文件，在 AstrBot WebUI 中的插件页面中选择 `从文件安装` 。
2. **安装依赖**：AstrBot 会在 bot 重启时自动安装所需依赖。如确有手动安装依赖之需求，可执行以下命令
    ```bash
    pip install -r requirements.txt
    ```
3. **重启 AstrBot**：我们推荐在安装本插件后手动重启一次 AstrBot。

---

## ⚙️ 配置

目前插件暂无需额外配置，所有规则通过命令创建。

---

## 💬 指令列表
| 指令        | 说明               |
|-----------|------------------|
| `mt add`  | 创建一则消息转发绑定的请求    |
| `mt bind` | 接受一则消息转发绑定的请求    |
| `mt del`  | 删除一条转发规则         |
| `mt list` | 列出与当前会话相关的所有转发规则 |
| `mt help` | 显示该插件帮助信息        |

---

## 🧩 项目结构
项目结构示例：

```
astrbot/
└─ data/
   └─ plugins/
      └─ astrbot_plugin_msg_transfer/
         ├─ LICENSE
         ├─ logo.png
         ├─ main.py
         ├─ metadata.yaml
         ├─ README.md
         └─ requirements.txt
```

同时，插件会建立 `astrbot/data/plugin_data/msg_transfer` 目录以存储持久化数据：

```
astrbot/
└─ data/
   └─ plugin_data/
      └─ msg_transfer/
         ├─ pending.json
         └─ rules.json
```
---

## 📦 功能概念

### **1. UMO（Unified Message Origin）**
一个唯一标识会话的字符串，例如：
```
aiocqhttp:GroupMessage:654321
wx:GroupMessage:123456
your_name:FriendMessage:114514
```

UMO 能让插件知道“某条消息来自哪个平台的哪个会话”，确保转发到正确目标。

---

### **2. 转发规则（Rules）**

插件允许你创建规则：
```
A → B
```

即来自端点 A 的消息会自动同步并转发给端点 B

规则格式例如：

```json
{
  "source_umo": "aiocqhttp:GroupMessage:654321",
  "target_umo": "your_name:FriendMessage:114514"
}
```

所有规则保存在 `astrbot/data/plugin_data/rules.json`

### **3. 消息链（MessageChain）处理**

插件会在转发前自动构造一条带来源信息的消息链，例如：

```
[转发] 张三 (1919810)
a:GroupMessage:11451419 -> a:GroupMessage:14191981

​这是一条示例消息
```

文字、图片、表情等组件都会被完整复制到目标平台。

## 🔄 转发行为示例
假设你有两条 UMO：

- `aiocqhttp:GroupMessage:654321`
- `your_name:FriendMessage:114514`

创建规则后：
- `aiocqhttp` 群 `654321` 的消息，会自动转发至 `your_name` 的好友 `114514`
- 会自动加上消息链前的来源标注

## 🤝 贡献

欢迎提交：

- Bug 报告
- 新特性建议
- PR（支持适配更多平台、更多规则类型）

插件完全开源，希望它能够成为 AstrBot 最好用的“跨群桥接插件”。
