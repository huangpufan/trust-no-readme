<div align="center">

# trust-no-readme

**别信 README，读源码。**

一个独立、客观、第三方的开源项目源码审查与公开披露档案：锁定精确 Commit SHA、附可点击源码直链与断网沙箱抓包、同步向上游提 Issue 推动修复，并经 CI 机械验真。

[English](./README.md) · [中文](./README.zh-CN.md) · [方法论](./METHODOLOGY.md) · [披露与上游提单](./DISCLOSURE.md) · [参与贡献](./CONTRIBUTING.md) · [AI Agent 指南](./AGENTS.md)

</div>

---

## 为什么建这个仓库

开源软件本来靠透明建立信任，但绝大多数人不会在 `pip install` 之前去翻几万行代码，于是不少高星项目把这种天然信任当成了保护伞：一边在 README 里写着“绝不传代码和密钥”“尊重关闭开关”，一边在源码里默认上传脚本和密码、拒合社区修复 PR、删掉隐私文档，甚至借用户自己的 AI Agent 绕过关闭开关。

现在用 AI Agent 做全量源码扫描和隔离沙箱验证的代价已经极低，开源仓库里的每一行代码和每一次提交历史实际上什么也藏不住。`trust-no-readme` 希望成为开源社区的一个独立第三方集散地：

- **持续审查主流开源项目**：锁定精确 40 位 Commit SHA，对照文档承诺、真实代码、Git/PR 历史与隔离沙箱实测。
- **公开可核验的完整证据**：以英文为正本并同步提供每份报告的中文版，清晰区分“拒修/绕过等产品取舍”与“无意工程回归”。
- **同步给上游提 Issue 推动修复**：发布同时向上游提交克制、可复现的工程 Bug Issue；维护者修复后新增不可变复审快照。
- **向人类与 AI Agent 开放 PR**：备好标准模板、脚手架与自动校验，任何人或 Agent 按格式带上可核验证据即可提交 PR，一起给开源社区排雷净化。

### 三条底线

1. **只靠固定版本源码和可复现事实说话**：每条发现锁定 40 位 Commit SHA、可一键跳转的源码行号、原文摘录与零凭据沙箱抓包；修复后新增不可变复审快照并将旧快照标为 `superseded`。
2. **契约违背（`H`）与开发机风险（`R`）严格分离**：违背显式承诺是一回事，默认高权限边界是另一回事，两者不混为一谈。
3. **CI 机械验真 + 负责任披露**：[`tools/validate.py`](./tools/validate.py) 只读拉取上游精确 SHA（含声明的伴生依赖仓）核验文件、行号、原文摘录与代码符号；未修补的可利用安全漏洞严格走 90 天私密披露（[`DISCLOSURE.md`](./DISCLOSURE.md)），不释放 0-day。

---

## 已追踪项目总表

<!-- CATALOG:BEGIN -->
当前追踪 **1** 个开源项目（共 **1** 份固定 SHA 快照）：

| 项目 | 版本 / SHA | 评级与结论 | 核心发现 | 上游 Issue | 报告 |
|---|---|---|---|---|---|
| [`browser-use/browser-use`](./audits/github/browser-use/browser-use/README.md) | `1.0.8`<br>`94784c1f` | `H4 / R3` · `E3+D`<br>**`Isolate`** | 持续141天上传脚本与密码（拒合PR#733）、46秒删光README遥测节、借编程Agent绕过关闭开关、连--help也开人物画像。 | [#6467](https://github.com/browser-use/browser-use/issues/6467) (existing) | [EN](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.md) · [中文](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.zh-CN.md) |
<!-- CATALOG:END -->

> **一分钟看懂标签**（完整规则见 [`METHODOLOGY.md`](./METHODOLOGY.md)）：
> - **契约一致性（`H0`–`H4`）**：`H0` 一致 · `H1` 措辞模糊/过期 · `H2` 重要默认行为未披露 · `H3` 默认代码直接违背显式承诺 · `H4` `H3` + 带日期的维护者知情记录。
> - **开发机风险（`R0`–`R4`）**：默认配置在存有 AI/云凭据工作站上的暴露面（`R3` = 默认高危宿主/凭据/外发暴露；`R4` = 默认无鉴权 RCE 或隐蔽外传）。
> - **证据层级**：`E3` = 源码全链实证 · `E3+D` = `E3` + 断网零凭据沙箱动态复现。

---

## 开放协作：欢迎人类与 AI Agent 共同参与

- **读者与技术选型**：查看上表（英文正本 + 同步中文版），或直接程序读取 [`data/catalog.json`](./data/catalog.json)。
- **上游维护者**：
  - 已修复问题？提交 [修复复审 Issue](https://github.com/huangpufan/trust-no-readme/issues/new?template=report-fix-reaudit.yml)（或直接提复审 PR）附上修复 SHA。
  - 发现我们读错了代码？提交 [异议纠错 Issue](https://github.com/huangpufan/trust-no-readme/issues/new?template=dispute-finding.yml) 附 `file:line` 反证。
  - 希望在项目卡片上挂出官方回应？按 [`templates/maintainer-response.md`](./templates/maintainer-response.md) 提交 PR 添加 `responses.md`。
- **社区贡献者与 AI Agent**：
  - 本仓库**面向 Agent 自感知设计**：让你的 Agent 读取 [`AGENTS.md`](./AGENTS.md) 或 [`llms.txt`](./llms.txt)，即可自动发现 Schema、报告/复审/上游提单模板（`templates/`）、脚手架（`tools/new_audit.py`）和结构化 JSON 校验器（`tools/validate.py --verify-upstream --format json`）。
  - 提名待审项目请走 [审计提名](https://github.com/huangpufan/trust-no-readme/issues/new?template=request-audit.yml)，提交新审计 PR 请看 [`CONTRIBUTING.md`](./CONTRIBUTING.md)。

## 许可协议

- 审计报告、方法论与模板（`audits/**`、`*.md`、`templates/**`）：**CC-BY-4.0**（引用须署名并保留固定 SHA 链接）。
- 校验脚本、测试与 Schema（`tools/**`、`tests/**`、`data/schema/**`、`.github/**`）：**MIT**。
