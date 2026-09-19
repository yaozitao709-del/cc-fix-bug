# cc-fix-bug

用于诊断与修复 CC Switch 中 Codex 供应商的悬空账号绑定的 Codex skill，适用于“切换失败：账号不存在”等故障。

当供应商的 `authBinding.accountId` 指向已删除的本地托管账号时，此 skill 可先诊断，再解除失效绑定或改绑到用户明确指定的本地账号。

## 安装到 Codex

需要 Git 和 Python 3.10 或更新版本；脚本仅使用 Python 标准库。

macOS / Linux：

```sh
git clone https://github.com/yaozitao709-del/cc-fix-bug.git ~/.codex/skills/cc-fix-bug
```

Windows PowerShell：

```powershell
git clone https://github.com/yaozitao709-del/cc-fix-bug.git "$env:USERPROFILE\.codex\skills\cc-fix-bug"
```

本仓库公开，任何人均可直接克隆，或[下载 ZIP](https://github.com/yaozitao709-del/cc-fix-bug/archive/refs/heads/main.zip)。若目标目录已存在，请先保留原目录，避免覆盖已有 skill。自定义 Codex 技能目录的用户应调整目标路径。

重新打开 Codex 后，在对话中输入：

```text
使用 $cc-fix-bug 诊断这台电脑上的 CC Switch 账号不存在或切换失败问题。
```

## 使用与修复边界

完整操作流程见 [SKILL.md](SKILL.md)。在仓库目录下，可先执行只读诊断：

```sh
python3 scripts/repair_binding.py diagnose
```

Windows 可将 `python3` 替换为 `py`。配置目录默认是 `~/.cc-switch`，自定义目录示例：

```sh
python3 scripts/repair_binding.py --cc-switch-dir /path/to/.cc-switch diagnose
```

- 修改前必须获得用户授权，并完全退出 Codex 和 CC Switch（包括托盘进程）。
- 只处理确认失效的账号绑定；有效绑定会被拒绝修改。
- 改绑必须使用本机诊断输出的 `local_id`，不能使用 `workspace_id`。
- 修改前备份数据库，失败时尝试回滚。
- 诊断输出不包含 OAuth 令牌，但包含账号邮箱和标识，请勿直接公开完整诊断结果。

## 测试

```sh
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```

现有 5 个测试覆盖悬空绑定识别、解除绑定、拒绝 workspace ID、显式改绑和保护有效绑定。数据库修复测试使用临时测试数据。

## 文件

- `SKILL.md`：技能说明与操作流程。
- `agents/openai.yaml`：Codex 展示信息与默认提示词。
- `scripts/repair_binding.py`：诊断与修复脚本。
- `scripts/test_repair_binding.py`：自动化测试。

本项目是独立的故障修复 skill，与 CC Switch 官方项目无隶属关系。
