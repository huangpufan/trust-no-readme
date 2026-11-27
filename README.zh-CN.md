<div align="center">

# trust-no-readme

**别信 README，读源码。**

一个独立、客观、第三方的开源项目源码审查与公开披露档案：锁定精确 Commit SHA、附可点击源码直链与断网沙箱抓包、同步向上游提 Issue 推动修复，并经 CI 机械验真。

[English](./README.md) · [中文](./README.zh-CN.md) · [监督成果](#独立监督成果与上游回应) · [项目总表](#已追踪项目总表) · [方法论](./METHODOLOGY.md) · [披露与上游提单](./DISCLOSURE.md) · [参与贡献](./CONTRIBUTING.md) · [AI Agent 指南](./AGENTS.md)

</div>

---

## 独立监督成果与上游回应

<!-- IMPACT:BEGIN -->
已审计 **2** 个项目（**2** 份 SHA 快照） · 主动提交上游 Issue **5** 个（另追踪已有 Issue 1 个、私密披露期 1 项） · **1** 项已验证修复（主干已合 `1` / 已发版 `0`） · **1** 项已确认待修 · **0** 项拒绝或未修关闭

### 已验证修复成果（独立监督推动）

| 项目 | 推动修复的 Issue 与缺陷 | 潜伏期 → 修复耗时 | 修复证明与发版状态 | 验真记录 |
|---|---|---|---|---|
| [`CherryHQ/cherry-studio`](./audits/github/CherryHQ/cherry-studio/README.md) | [#25579](https://github.com/CherryHQ/cherry-studio/issues/25579) · `F2`<br>PlantUML 代码块零点击外发完整图源至 www.plantuml.com | 存在 `431` 天 → **7.5小时** 合入 | Commit [`f59df558`](https://github.com/CherryHQ/cherry-studio/commit/f59df558c95c7e571c45cd39d604250abce2ac6d) · PR [#25582](https://github.com/CherryHQ/cherry-studio/pull/25582)<br>**主干已合（待发版，`v2.2.3` 仍受影响）** | `2026-11-27` 源码验真 |

> **历史保留口径**：已验证修复记录的是独立监督推动的真实改进与上游改错意愿，**绝不抹除或改写旧快照**；项目复审更新最新评级后，历史峰值评级与原快照仍永久保留并列展示。所有修复均需源码验真且由 CI 机械核验上游 commit 存在与祖先关系。
<!-- IMPACT:END -->

---

## 已追踪项目总表

<!-- CATALOG:BEGIN -->
当前追踪 **2** 个开源项目（共 **2** 份固定 SHA 快照）：

| 项目 | 最新版本 | 当前评级 vs 历史峰值 | 核心发现 | 上游响应与 Issue | 报告 |
|---|---|---|---|---|---|
| [`CherryHQ/cherry-studio`](./audits/github/CherryHQ/cherry-studio/README.md) | `v2.2.3`<br>`62867d8f` | `H3 / R3` · `E3+D`<br>**`Isolate`** | 国内版传原始accountId发美国PostHog画像（国际版匿名）、431天零点击外发PlantUML图源、关隐私仍发Client-Id与第三方IP定位、手动导MCP默认授信。 | **积极修复中**<br>✓ [#25579](https://github.com/CherryHQ/cherry-studio/issues/25579) (主干已验真)<br>◐ [#25581](https://github.com/CherryHQ/cherry-studio/issues/25581) (已确认待修)<br>○ [#25580](https://github.com/CherryHQ/cherry-studio/issues/25580) (已分诊) | [EN](./audits/github/CherryHQ/cherry-studio/2026-11-27-62867d8f.md) · [中文](./audits/github/CherryHQ/cherry-studio/2026-11-27-62867d8f.zh-CN.md) |
| [`browser-use/browser-use`](./audits/github/browser-use/browser-use/README.md) | `1.0.8`<br>`94784c1f` | `H4 / R3` · `E3+D`<br>**`Isolate`** | 持续141天上传脚本与密码（拒合PR#733）、46秒删光README遥测节、借编程Agent绕过关闭开关、连--help也开人物画像。 | **仅社区 PR·维护者未回复**<br>○ [#6541](https://github.com/browser-use/browser-use/issues/6541) (社区 PR #6545)<br>○ [#6542](https://github.com/browser-use/browser-use/issues/6542) (社区 PR #6549)<br>· [#6467](https://github.com/browser-use/browser-use/issues/6467) (已有 · 社区 PR #6470) | [EN](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.md) · [中文](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.zh-CN.md) |
<!-- CATALOG:END -->

> **一分钟看懂标签**（完整规则见 [`METHODOLOGY.md`](./METHODOLOGY.md)）：
> - **契约一致性（`H0`–`H4`）**：`H0` 一致 · `H1` 措辞模糊/过期 · `H2` 重要默认行为未披露 · `H3` 默认代码直接违背显式承诺 · `H4` `H3` + 带日期的维护者知情记录。
> - **开发机风险（`R0`–`R4`）**：默认配置在存有 AI/云凭据工作站上的暴露面（`R3` = 默认高危宿主/凭据/外发暴露；`R4` = 默认无鉴权 RCE 或隐蔽外传）。
> - **上游响应与修复追踪**：独立于 `H/R` 的第三维台账（[`tracking.json`](./data/schema/tracking.schema.json)），客观区分已验真修复（主干已合 vs 正式发版）、维护者确认待修、社区 PR 待审核、长期静默与拒绝修复（`wontfix` / `closed_unfixed`）。
> - **证据层级**：`E3` = 源码全链实证 · `E3+D` = `E3` + 断网零凭据沙箱动态复现。

---

## 为什么建这个仓库

开源软件本来靠透明建立信任，但绝大多数人不会在 `pip install` 之前去翻几万行代码，于是不少高星项目把这种天然信任当成了保护伞：一边在 README 里写着“绝不传代码和密钥”“尊重关闭开关”，一边在源码里默认上传脚本和密码、拒合社区修复 PR、删掉隐私文档，甚至借用户自己的 AI Agent 绕过关闭开关。

现在用 AI Agent 做全量源码扫描和隔离沙箱验证的代价已经极低，开源仓库里的每一行代码和每一次提交历史实际上什么也藏不住。`trust-no-readme` 希望成为开源社区的一个独立第三方集散地：

- **持续审查主流开源项目**：锁定精确 40 位 Commit SHA，对照文档承诺、真实代码、Git/PR 历史与隔离沙箱实测。
- **公开可核验的完整证据**：以英文为正本并同步提供每份报告的中文版，清晰区分“拒修/绕过等产品取舍”与“无意工程回归”。
- **同步给上游提 Issue 并长期追踪回应与修复**：记录维护者响应速度、源码验真后的真实修复（严格区分主干已合与正式发版）、长期静默与拒绝修复；修复后更新项目最新结果，同时永久保留历史峰值与旧快照。
- **向人类与 AI Agent 开放 PR**：备好标准模板、脚手架与自动校验，任何人或 Agent 按格式带上可核验证据即可提交 PR，一起给开源社区排雷净化。

### 三条底线

1. **只靠固定版本源码和可复现事实说话**：每条发现锁定 40 位 Commit SHA、可一键跳转的源码行号、原文摘录与零凭据沙箱抓包；修复后新增不可变复审快照、更新当前结论，同时永久保留历史峰值与标为 `superseded` 的旧快照。
2. **契约违背（`H`）、开发机风险（`R`）与上游响应状态三轴独立**：违背承诺是一回事，默认权限边界是一回事，被指出问题后是否跟进修复是另一回事，三者各自按客观事实独立记录、互不遮蔽。
3. **CI 机械验真 + 负责任披露**：[`tools/validate.py`](./tools/validate.py) 只读拉取上游精确 SHA（含伴生依赖仓与每条声明修复的 `fix_commit`）核验文件、行号、原文摘录与真实提交；未修补的可利用安全漏洞严格走 90 天私密披露（[`DISCLOSURE.md`](./DISCLOSURE.md)），不释放 0-day。

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
