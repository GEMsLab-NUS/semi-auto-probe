# AI Agent 执行接口与维护说明 — v2

## 本次解决的问题

旧实现将帮助与执行关键词混用，目录没有覆盖新的测量流程；靠状态文字中的
`saved/done` 推进步骤，寻找下一步时还可能跳过运行/失败步骤。后台规划读取 Tk
变量、异常回调引用已释放变量、取消后旧响应回写，也影响稳定性。

当前实现保留原页面与仪器流程，加入帮助路由、测量检查、类型化结果和串行状态机。

## 接口

| 模块 | 责任 |
| --- | --- |
| `agent_knowledge.py` | 当前帮助、12 个 prompt 模板；随 wheel 打包。 |
| `agent.py` | Context/Plan、动作目录、参数 schema、本地验证、规则/LLM 规划。 |
| `agent_runtime.py` | `inspect_measurement(app)`、`DesktopAgentRuntime`，适配桌面工作流。 |
| `ui/agent_panel.py` | 对话、权限模式、步骤和结果展示。 |
| `agent_demo.py` | 离线规划和模拟 UI，不创建硬件客户端。 |

```python
# Tk 主线程：采集真实状态，包含测量预检
snapshot = app.agent_context()
# 后台线程：只读取普通 Python 数据
plan = app.agent_planner.plan(instruction, snapshot)
# Tk 主线程：执行模式允许时，再检查并启动下一步
result = app.agent_runtime.execute(plan)
# AgentExecutionResult(status, message, action_id, run_id)
```

新调用者使用 `DesktopAgentRuntime.execute`；`ProbeApp.execute_agent_plan` 是保留的
旧动作适配器，不是提供给模型的通用接口。异常转为 `failed`。

结果状态为 `running/done/blocked/failed/cancelled`；run_id 标识 UI 的当前执行步骤，
不匹配的结果不能改变状态。后台规划结果通过队列回到 Tk；后台不读取 Tk，也不调用 Tk after。
每次规划带 generation，取消/停止使旧响应失效。

## 完成、失败与停止

前面所有步骤都是 done 时才能执行下一 pending。running/blocked/failed/cancelled
均阻断后续步骤；不自动跳过失败或重试。

`_handle_worker_event` 先运行原事件处理、更新结果/释放占用，再调用 runtime.observe。
状态消息只显示，不驱动完成。

| 动作 | 终止证据 |
| --- | --- |
| stage/GDS | 匹配来源的 read_positions，核对期望位置，再等待 motor_done。 |
| AutoFocus | autofocus_done，未停止且无 autofocus_error。 |
| FocusMap | af_plane_done，未停止、无错误并保存有效平面。 |
| ImgStitch/stack | imgstitch_done 记录输出，imgstitch_finished 后才完成。 |
| Probe AutoTest | autotest_done 记录完成，autotest_finished 后释放资源。 |
| Wire-bond | mapping_finished 的真实状态/错误；忽略嵌套 AutoTest 清理事件。 |
| 单帧/导航/关联 | 同步结果；每次 cv2.imwrite 都检查返回值。 |

旧工作线程尚未传递 run_id，适配依赖应用工作流互斥与事件来源匹配。未来要支持
并发，必须将 run_id 传入事件生产者，不能直接扩大并发。没有收到终止证据时，
不会因超时或空闲状态猜测成功；应检查原工作流并使用 Stop。

- Cancel Plan：取消待执行部分；废弃晚到的规划响应；当前硬件继续运行。
- Stop：调用现有停止方法，取消待执行步骤；当前步骤等实际清理结束再终止。
- Conversation Only：只回答/预览，禁用任何执行。
- Authorized Step（默认）：Run Next Step 逐步执行，包括导航。
- Auto Step：用户选择此模式后自动顺序执行，仍逐步检查，不越过占用或失败。

## 检查与测量配置

规划和实际执行共用 context_blockers_for_action / action_parameter_blockers。
确认与硬件效果由本地代码决定，模型不能降低要求。

检查运动/键盘/回读、AutoFocus/FocusMap/ImgStitch、AutoTest/Mapping、ImgMatrix/EdgeTrace
占用，以及连接、相机、GDS 绑定/目标、网格与图像等前置条件。探针接触时不直接启动
独立移动/聚焦/成像扫描；Probe AutoTest 使用自身退针/接触流程，Wire-bond 要求 Separate。
动态 blocker 单独记录，刷新只移除已经消失的本地问题，不删除模型声明的澄清问题。

`inspect_measurement` 不连接硬件、不写文件：

1. 读取当前 Config Measure Flow 与模式对应的扫描设置，检查图结构与几何。
2. Wire-bond 复用 Mapping 参数检查；Probe 检查 LayoutMap/FocusMap，并保留自定义点表；Photo 检查帧。
3. 返回模式、点数、节点参数、blockers、fingerprint 和设置对象。
4. fingerprint 覆盖设置、实际点位、应用配置与 FocusMap 模型。只发送 digest，
   不发送整个可能含凭据的应用配置。
5. `/run measurement` 绑定 fingerprint，执行前重读比较；变化则阻止启动并要求重做计划。

运行复用 start_autotest 的 Probe/Wire-bond 分派，沿用测量归档、曲线和 Plot Panel。
本地预检不保证仪器可连接或采集成功。快速 UI 刷新复用最近测量快照；Send 和执行重新检查，
不在高频状态更新中重新生成完整扫描计划。

独立成像/聚焦继续使用执行时当前 UI 参数，尚未绑定完整参数快照；确认前核对所属页面设置。
自然语言自由编辑测量图和全部手动仪器命令尚未开放。

## 界面与扩展

保留左侧显微镜/坐标/状态与中间对话；右侧为紧凑 Steps、Help、Templates、Preview。
模板只填入输入框。原可视化放在 Preview；对话占主要宽度，不新增仪器页面。
layout_image_overlay 只关联已有图像路径并导航，不自动配准。

新增执行能力需同步动作目录/schema、本地参数/状态检查、执行适配/终止事件、帮助、模板和
成功/失败/停止测试，不能只更新 prompt。

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_agent*.py' -v
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --all
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --all --ready
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --preview --ready
```

测试/演示不需要 API key 或设备；真实驱动、串口、相机、光源与在线模型仍需实际联调。
见 [模板](agent-test-templates.md) 与 [provider contract](agent-function-spec.md)。
