---
name: cc-fix-bug
description: Diagnose and safely repair CC Switch Codex errors such as “切换失败：账号不存在” caused by dangling managed ChatGPT account bindings on Windows, macOS, or Linux.
---

# CC Switch 悬空账号绑定修复

用于 CC Switch 的 Codex 供应商报“账号不存在: <UUID>”、无法切换、无法编辑或无法删除当前卡片的情况。核心故障是 `providers.meta.authBinding.accountId` 指向 `codex_oauth_auth.json` 中已不存在的本地账号 ID。

## 安全边界

- 先诊断，确认数据库完整且绑定确实悬空；不要把 WorkspaceId 当作 LocalId。
- 不读取、打印或传输 access token、refresh token、id token。
- 不根据相同邮箱或 ChatGPT workspace 自动改绑账号；同一 workspace 可能属于多个用户。
- 修改前必须获得用户对本次修复的授权，并让用户完全退出 Codex 和 CC Switch（包括托盘进程）。
- 每次修改只处理用户确认的一个供应商；脚本会备份数据库并在失败时回滚。
- 如果绑定的 LocalId 仍存在，拒绝修改：这不是本 Skill 处理的悬空引用。
- 如果账号归属不明确，优先 `detach`。解除绑定后，Codex 官方卡会沿用 Codex 当前官方登录；不要擅自选另一个托管账号。

## 工作流

1. 识别操作系统和 CC Switch 配置目录。默认是当前用户主目录下的 `.cc-switch`；自定义目录使用 `--cc-switch-dir`。
2. 运行只读诊断：

   ```text
   python <skill>/scripts/repair_binding.py diagnose
   ```

   Windows 优先使用 `py`，macOS/Linux 使用可用的 `python3`。诊断输出只包含账号邮箱、本地 ID、workspace ID、供应商绑定和当前 Codex 登录的匹配结果，不包含令牌。
3. 只有同时满足以下条件才进入修复：
   - `database_check` 为 `ok`；
   - 目标供应商的 `status` 为 `dangling`；
   - 用户确认目标供应商；
   - CC Switch 已完全退出。
4. 根据用户意图选择一种修复：
   - 当前 Codex 登录应继续使用，或无法证明原账号属于哪个托管记录：

     ```text
     python <skill>/scripts/repair_binding.py detach --provider-id <provider-id>
     ```

   - 用户明确指定认证中心中某一行显示的 LocalId：

     ```text
     python <skill>/scripts/repair_binding.py rebind --provider-id <provider-id> --account-id <local-id>
     ```

     `--account-id` 必须是诊断输出 `accounts[].local_id` 中的值，不能使用 `workspace_id`。
5. 确认输出 `result: success`、`database_check_after: ok` 和备份路径。重新打开 CC Switch，验证目标卡片可切换；若账号显示过期，在认证中心对目标账号执行就地“重新登录”，不要删除后重新添加。

## 失败处理

- `CC Switch appears to be running`：先退出应用和托盘进程，再重试；不要跳过进程检查。
- `no dangling binding` 或 `bound account still exists`：停止，不要强制修改。
- `account ID is not a local managed account`：用户给的是 workspace ID 或另一台电脑的 ID；在本机重新诊断并选 `local_id`。
- 数据库检查失败、JSON 无法解析或账号存储结构异常：停止并报告，不进行修复。
- 修改后应用仍报错：用脚本输出的备份路径恢复数据库前，先停止 CC Switch；不要叠加第二种修复。

## 原因说明

常见触发方式是删除并重新添加托管 ChatGPT 账号，或旧版本升级后账号主键由 workspace ID 改为本地 UUID，但供应商仍保留旧引用。新 LocalId 是每台电脑本地生成的，不能从另一台电脑复制。相关上游记录：GitHub Issues `farion1231/cc-switch#6969`、`#7032`、`#7055`。
