# 提交材料清单（SUBMISSION）

对照赛题「四、提交材料」逐项核对。

| 材料 | 要求 | 状态 | 位置 |
| --- | --- | --- | --- |
| 项目网站网址 | Qoder Sites 发布，可见范围「任何人都可访问」 | 待发布 | 在 Qoder 中用 `site/` 目录（或由 Qoder 直接托管本应用）创建站点后填写 |
| 代码仓库 | 完整源码，公开或对评审开放 | 就绪 | 本仓库分支 `2026-competition-ecosystem` 的 `qoder/paperradar/` |
| 运行说明 | 环境、依赖、部署接入方式、完整步骤 | 就绪 | [RUNBOOK.md](RUNBOOK.md) |
| 验证样例 | 初始数据、输入、步骤、预期结果、技术作用记录 | 就绪 | [VERIFY.md](VERIFY.md) |
| Qoder 开发记录 | 关键环节截图/录屏 + 账号 UID | 待补充 | [QODER_LOG.md](QODER_LOG.md) |
| 使用效果材料 | 鼓励提交：使用人数、提效对比、反馈 | 待补充 | 使用后补充访问统计截图、问卷结果 |

## 提交前检查

- [ ] 用浏览器无痕窗口打开项目网站，确认未登录状态可访问
- [ ] 网站包含：项目简介、解决的问题、核心功能、截图/演示视频、仓库链接、体验入口
- [ ] 评审机器按 RUNBOOK 能一次跑通：seekdb → PowerContext → bootstrap → uvicorn → smoke_test 全 PASS
- [ ] `.env.example` 中只有占位值，仓库内无真实凭据
- [ ] QODER_LOG.md 补齐 UID、commit hash、截图，网站地址回填
- [ ] 评审结束前不暂停/不删除站点；若修改了网站内容，记得重新点「发布」
