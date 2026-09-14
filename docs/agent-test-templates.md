# AI Agent 可直接测试的模板

AI Agent 右侧 Templates 选择条目 → Load into input → Send。
加载不会发送或执行。默认 Authorized Step，计划后点击 Run Next Step。
只看回答/计划选 Conversation Only；Auto Step 会自动执行可运行步骤。

## 无设备试用

```powershell
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --list
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --all
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --all --ready
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --all --ready --output .runtime/agent-v2/templates-ready.jsonl
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --template focus_capture --ready
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --preview --ready
```

`--ready` 只是虚构就绪状态，不代表真实连接。命令行只生成计划；模拟 UI 可以运行/停止，
所有结果标为 SIMULATION，不创建硬件客户端或测量文件。演示始终使用本地规则，无需 API key。
`--output` 以 UTF-8 保存逐行 JSON，便于比较或接入自己的测试脚本。

## 可复制到正式界面的 prompt

| ID | Prompt | 预期 |
| --- | --- | --- |
| help | `/help` | 功能概览，零执行步骤。 |
| status | `/status` | 真实当前状态与能力。 |
| workflow_help | `/help workflow` | Workflow v2、数据/控制线、公式、Plot Panel。 |
| mapping_help | `/help mapping` | Probe 与 Wire-bond 差异。 |
| open | `/open AutoTest` | 打开原页面，不启动测量。 |
| check | `/check` | 本地测量图/几何检查，不连接仪器。 |
| capture | `capture and save one image` | 有帧才执行；保存唯一文件并更新最近图像，不清空或覆盖 ImgStitch 会话。 |
| focus_capture | `Run autofocus, then capture and save one image` | 聚焦完成并释放占用后才允许拍照。 |
| stage | `move relative X=10 pulses` | 相对 X 移动 10 pulse；执行前核对方向和行程。 |
| measure | `/run measurement` | 预览当前模式/点数/参数，确认后执行。 |
| help_intent | `如何运行 autofocus？` | 只解释，不产生 Z 移动。 |
| unsupported | `执行任意 Python 调整所有仪器` | 请求澄清，不执行代码。 |

代码中的模板定义集中在 agent_knowledge.AGENT_TEMPLATES，此表为操作说明。

## 测量试用顺序

1. AutoTest → Config Measure Flow，加载当前库的
   General / Examples / Current and Heatmap 或 General / Examples / Curve Feature Branch。
   这些是真实测量示例，不是无设备模拟，需配置对应仪器与限值。
2. 根据当前模式配置 Probe 点阵/自定义点表，或 Wire-bond Mapping 区域。
3. Send `/check` 并解决问题；通过只表示本地检查通过，不代表仪器已连接。
4. Send `/run measurement`，阅读右侧模式、点数、节点参数后 Run Next Step。
5. 原 AutoTest/Mapping 和 Plot Panel 展示、保存实际结果。

配置变化测试：生成运行计划后修改流程或扫描区域，再运行旧计划，应阻止启动并要求重新生成。

## 状态机测试

- 聚焦→拍照的第一步运行中再次 Run Next Step：不能提前拍照。
- 运行中 Cancel Plan：当前聚焦继续，后续拍照取消；要停硬件用 Stop。
- 生成计划但尚未运行就 Stop：待执行步骤全部取消。
- 第一步运行中 Stop：等待原工作流清理，不能自动继续第二步。
- Conversation Only 加载任意硬件模板：只预览计划。
- API 很慢时 Cancel Plan：晚到结果不能重新激活计划。
- 相机未开启时 capture：提示缺帧；开启后重新检查/规划再执行。
- 连续两次 capture：生成两个不同文件；现有 `session.json`、`stack_result.png` 和 `tiles/` 内容保持不变。
- `/open MainView`、`/open LayoutMap`、`/open SerialIO`、`/open Settings`：分别打开 Main、LayoutBond、Communication、Config，并用当前可见页名回复。

## 验证范围

自动测试覆盖帮助路由、模板、参数约束、配置变化、完成/失败/停止事件、取消旧响应、
UI 模式与线程传递。完整 unittest 回归检查共享事件处理与原工作流。
离线验证不替代真实台体、探针、串口、相机、仪器和在线模型联调。
