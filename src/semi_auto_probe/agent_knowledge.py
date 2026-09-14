"""Bundled, offline help and reproducible prompts for the desktop assistant.

No hardware imports: this knowledge is also available in an installed wheel.
Executable actions remain governed by agent.build_agent_capabilities.
"""
from __future__ import annotations

HELP_TOPICS = {
    "overview": """## 当前助手 / Current assistant
Help 解释功能与前置条件；Plan 生成步骤；Run Next Step 执行确认的步骤。
Conversation Only 仅回答与预览；Authorized Step 逐步确认；Auto Step 按本地检查顺序执行。
动作结束后才开放下一步；Stop 请求停止并取消剩余步骤。取消计划不会停止已启动的硬件。
支持：状态、功能帮助、页面导航、移动、AutoFocus、拍照、ImgStitch、FocusMap、当前测量流程检查与运行。
测量使用 AutoTest 的 Config Measure Flow；Wire-bond 模式使用 Mapping 区域，Probe 模式使用 LayoutMap 和 FocusMap。
模型不能直接发串口命令、执行 Python、任意改写仪器参数或生成未实现的仪器动作。
用 /help workflow、/help mapping、/help instruments、/help imaging 查看具体帮助。
""",
    "workflow": """## Measurement Workflow v2
在 AutoTest 打开 Config Measure Flow，从器件分类模板库选择流程。
双击卡片打开参数栏；控制线连接 Start、测量、条件和 End；数据线连接输出与消费者。
条件 True/False 都需要接线。Plot Panel 支持 1×1、1×2、2×2、2×3，每格一个源和一种物理量。
live 是实时曲线族，curve 是完整曲线族；公式在完整曲线到达后计算。
Math Formula 支持命名输入、常数、积分 integral、均值 mean、斜率 slope 等受限表达式。
参数使用原始 SI 单位。模板与保存文件为 version 2；不读取旧版工作流。
先用 /check 检查当前图和扫描几何，再 /run measurement 生成运行步骤。图检查通过不等于仪器连通性验证。
当前助手不会替你写任意测量图或设置电压、电流限值；在编辑器确认这些参数后再生成运行计划。
参考：docs/measurement-workflow-v2.md、docs/device-workflow-templates.md。
""",
    "mapping": """## Probe / Wire-bond Mapping
Probe AutoTest：先绑定 LayoutMap，运行或加载 FocusMap，再配置点阵/点表与共享测量流程。
Wire-bond：管理员在 Config 开启模式；在 AutoTest 的 Mapping 区域配置矩形/圆形/多边形、步长和扫描方式。
Mapping 固定 Z 扫描，每个稳定位置执行同一个 Config Measure Flow；开始前探针必须是 Separate。
Plot Panel 热力图使用计划中的零基行列，与蛇形采集顺序无关；完成后点击格点可回看曲线。
缺失、跳过、失败、中断有独立状态；关闭绘图窗口只隐藏窗口，不停止采集。
Virtual Mask、TEMT6000 响应与光源校正请选择对应器件模板并检查物理参数。
参考：docs/measurement-workflow-v2.md、docs/temt6000-irradiance.md。
""",
    "instruments": """## 当前仪器与功能边界
现有测量执行器包括 Keithley 2450、HP6614C、B1500 和 DP100 光源相关卡片。
可使用已实现的 IV、定偏压/光响应、光脉冲、光源校正等流程；具体端口由 workflow_ports.py 注册。
旧 IT、Light Control、Light Pulse、Switch Wavelength 是未实现的占位卡片，v2 预检拒绝它们。
DP100 需要本地 DLL / 可选 pythonnet 环境及真实设备；TEMT6000 辐照度依赖响应和校正参数。
助手能解释、检查并调用已配置的高层流程；不把未探测的仪器报告为已连接，不绕过 compliance 或探针检查。
连接和量程/限流等参数仍在现有仪器/流程界面设置。
参考：docs/device-workflow-templates.md、docs/display-calibration-host.md。
""",
    "imaging": """## 成像与移动
Main 提供相机和台体控制；AutoFocus 用当前参数搜索 Z；FocusMap 用已有网格拟合样品平面。
ImgStitch 执行当前 XY / T-stack / Z-stack 设置；单帧拍照保存为独立文件并更新最近图像，不会清空或覆盖现有 ImgStitch 会话。
LayoutBond 使用拟合的 GDS↔stage 映射；移动到选点前必须选中目标并完成绑定。
stage_move 数值单位是控制器 pulse；zero 是移动到坐标零点，不是限位寻零或重新定义零点。
layout_image_overlay 只关联最新图像路径并打开 LayoutBond，不自动执行图像配准。
ImgMatrix、EdgeTrace、相机连接、手动仪器配置可以通过页面操作；助手目前仅提供帮助/导航。
""",
}

# id, label, prompt, expected behavior. Loading only fills the composer.
AGENT_TEMPLATES = (
    ("help", "01 · Help / 无硬件", "/help", "回答能力边界；零执行步骤。"),
    ("status", "02 · Status / 无硬件", "/status", "显示当前状态；不启动硬件。"),
    ("workflow_help", "03 · Workflow v2", "/help workflow", "解释控制线、数据线、公式及 Plot Panel。"),
    ("mapping_help", "04 · Mapping help", "/help mapping", "解释 Probe / Wire-bond 的不同前置条件。"),
    ("open", "05 · Open AutoTest", "/open AutoTest", "生成打开已有页面的步骤。"),
    ("check", "06 · Check current flow", "/check", "读取当前流程/几何，报告问题；不连接仪器。"),
    ("capture", "07 · Capture one frame", "capture and save one image", "相机有帧时保存独立文件并更新最近图像；不会清空 ImgStitch 会话。"),
    ("focus_capture", "08 · Focus → capture", "Run autofocus, then capture and save one image", "聚焦结束并释放占用后才允许拍照。"),
    ("stage", "09 · Relative X (10 pulse)", "move relative X=10 pulses", "生成相对移动，确认前核对方向与空间。"),
    ("measure", "10 · Run current measurement", "/run measurement", "显示模式、点数、节点和配置签名；确认后运行当前流程。"),
    ("help_intent", "11 · Help intent regression", "如何运行 autofocus？", "只解释操作方法，不产生 Z 移动。"),
    ("unsupported", "12 · Unsupported action", "执行任意 Python 调整所有仪器", "请求澄清，不执行任意代码。"),
)


def help_markdown(topic: str = "overview") -> str:
    key = topic.strip().lower()
    aliases = {"测量": "workflow", "流程": "workflow", "仪器": "instruments", "成像": "imaging", "autofocus": "imaging", "focusmap": "imaging", "imgstitch": "imaging", "layoutbond": "imaging"}
    key = aliases.get(key, key)
    return HELP_TOPICS.get(key, HELP_TOPICS["overview"])


def knowledge_markdown() -> str:
    return "\n\n".join(HELP_TOPICS.values())
