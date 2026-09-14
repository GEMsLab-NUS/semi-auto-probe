# Measurement Workflow v2

桌面 AutoTest 与 Wire-bond Mapping 共用节点接口、公式解释器和 Plot Panel。
手动仪器窗口、已有采集参数及原始 CSV/JSON 保存路径继续使用原实现。

## 使用

节点画布采用深灰网格、分类色标题栏和彩色类型端口，选中节点以橙色边框标识。
单击卡片选中，拖动卡片移动；双击标题打开参数栏，再次双击同一卡片或点击 Close 关闭。
参数栏打开后，单击其他卡片不会切换正在编辑的对象；双击其他卡片才切换。
每类节点直接显示最多三个常用参数（如 IV 起止电压和步长、定偏压和电流限值、公式及输出类型）。
点击参数值即可输入或选择，Enter 或离开输入框提交，Esc 取消；非数值及非有限数不会写入数值参数。
节点编辑与参数栏、保存文件使用同一份参数；高级参数仍在参数栏编辑。缩放低于 65% 时隐藏参数细节。

从输出端口拖动连线时，兼容输入高亮，靠近端口会吸附；Esc 可取消连线。
悬停端口可在底部查看完整名称和类型。滚轮以指针位置为中心缩放，中键或空白处拖动平移。
连线优先使用平滑曲线，遇到节点阻挡则沿避障路线圆滑转弯；直线段交叉处保留跨线提示。
右键拖动按实际可见路径切断连线。若卡片重叠封住端口，显示红色短线并在拖动结束后提示移动卡片。

1. 打开 **Config Measure Flow**。可以从库中加载 **General / Examples / Current and Heatmap**，
   或 **General / Examples / Curve Feature Branch**。
2. 用控制线连接 Start、测量/校正、条件分支和 End。条件的 True/False 都要接线；允许分支重新汇合。
   数据卡片不串进控制路径，数据线不会再次运行仪器。
3. 在测试卡片的 Result Outputs 中选择输出端口。`live` 是实时电流曲线族，
   `curve` 是完成后的完整电流曲线族；`*_voltage` 和 `*_resistance` 分别表示电压和逐点 V/I。
   数值特征端口附带单位。仪器没有提供的特征明确标记为不可用，不从别的测试猜取。
4. 添加一个 **Plot Panel**，选择 1×1、1×2、2×2 或 2×3。子图从左到右、从上到下编号。
   将实时输出或数值特征连到对应 Slot；也可以在各子图设置页的 source 下拉框选择。
5. 每个格子只接一个源和一种物理量。同一测试、同一物理量的曲线族可以一起显示。
   `Curve` 设为 `all` 显示整个曲线族，或填写实际曲线标签固定选择一条。
6. 每格可配置标题、X/Y 线性或对数轴、自动范围或 `最小值,最大值`；
   热力图还可配置色标范围和对数色标。数值使用原始 SI 单位，例如 `1e-9,1e-5`。

热力图的坐标是扫描计划的零基行/列，与测量点身份绑定，不依赖蛇形扫描的采集次序。
蓝色星标表示当前正在测试的格点。整点 workflow 结束后才提交该格点的特征值；
有值时按色标着色，缺失为灰色、分支跳过为深灰色、失败为红色、中断为橙色。
对数显示屏蔽非正值并提示，原始数据不改动。

开始新点会清空实时曲线，但保留此前热力图。测试结束后点击热力图格点可回看该点保存的曲线。
运行中点击不切换回放，避免混入其他测量点的数据。窗口关闭按钮隐藏窗口，不停止采集。

## 数学与判断

**Add / Subtract / Multiply / Divide** 是四个独立的双输入卡片。
未接线的输入使用 Constant A/B；接了线但上游未执行时，不能退回常数冒充结果。

**Math Formula** 支持最多 16 个命名输入：

```text
Inputs: signal:curve,area:number
Constants: {"area": 0.01}
Formula: integral(signal) / area
Result type: number
Result unit: C/m2
```

Inputs 使用 `name:number`、`name:bool`、`name:curve`。更改后点 **Apply input definitions**
刷新连接选择器；删除输入会断开其连线。曲线输入通过 `Curve label` 选择具体曲线，
`last` 表示最后完成的曲线。曲线计算在测量节点结束后进行，不对实时局部数据做最终分支判断。

支持：

- 算术 `+ - * / % **`，比较 `> >= < <= == !=`，逻辑 `and or not`，条件表达式 `a if predicate else b`。
- 常量 `pi`、`e`；函数 `abs sqrt log log10 exp sin cos min max mean std peak integral slope segment`。
- `std` 是总体标准差；`peak` 是最大 Y 值；`integral(curve)` 按实际 X 和采集次序做梯形积分。
- `slope(curve)` 是 Y 对 X 的最小二乘直线斜率；不排序 X，不把索引当作采样时间。
- `segment(curve, start, stop)` 按采样索引切片，包含 start、不包含 stop。

公式示例：`log10(abs(a)/b)`、`mean(signal)`、`slope(segment(signal, 0, 100))`。
布尔示例：`a >= lower and a <= upper`，Result type 必须选 `bool`，再连 Boolean Branch。
Number Compare 保留单输入阈值比较，Compare A/B 提供双输入比较。

编辑时的 Example preview 使用常数和合成示例数据，**不是实际测量结果**。
公式通过受限 AST 解释器执行；不允许导入、属性访问、任意函数或 Python 脚本。
限制表达式长度、节点数和指数范围。

除零、非法函数域、非有限数、空曲线、缺失上游等产生带原因的无效值。
默认分支走 False、热力图留空；相关卡片的 Invalid result 可改为 `stop`。
用户主动停止时保留 incomplete 状态，不因缺少最终特征再次升级为计算失败。

## 数据与执行接口

- `workflow_ports.py` 是编辑器和执行器共享的端口注册表。
  端口类型包括 number、bool、curve（完整曲线）、trace（显示流）、calibration、waveform。
  panel 的 plot 输入只接受 number 或 trace。
- `workflow_formula.Curve` 保存 X/Y、曲线标签及单位；`InvalidValue` 保存错误原因。
- `workflow_runtime.NodeCapture` 属于单节点、单测量点、单运行。
  结果明确包含 `run_id/node_id/point/status/valid/outputs`、特征统计以及完整曲线。
  `outputs` 记录各特征有效状态、原因和单位。
- 测试执行器直接返回结果；不再按文件修改时间选择“最新结果”。
  配套光源使用独立线程上下文，不共享测量节点的采样收集器。
- 新图在运行前检查 Start/End、端口类型、悬空引用、控制/数据环以及两条条件出口。
  已连线的校正源必须在到达消费者的每条路径上先执行。
- 旧的 IT、Light Control、Light Pulse、Switch Wavelength 卡片原本没有仪器执行器。
  v2 预检明确拒绝这些占位卡片；使用已实现的定偏压/光响应和 DP100 卡片。
  B1500 的现有驱动回调按完成的曲线提供数据，因此 panel 也按曲线到达更新。

## 保存格式

工作流文件统一为 version 2，模板名称不带版本号；不再读取 version 1 或无版本文件。
旧数学 operator 参数和 Mapping Heatmap 不再转换或从模板库提供，请使用表达式/独立算术卡和 Plot Panel。
个人文件不会自动覆盖。模板树按器件分类，同一器件流程可用于 Probe 或 Bonding 位置扫描。


## 刷新与验证

实时刷新上限 20 FPS；在线 min/max 摘要随采样更新，显示开销不随原始历史长度线性增长。
每个生产者最多保留一个待处理显示通知，新的显示快照覆盖旧快照。
采样保存、完成和错误事件不通过这个可合并通道。

每个子图使用独立 Figure/Canvas；复用曲线、图像、色条和星标。
坐标、几何或图例不变时局部 blit；坐标范围/窗口变化时重建背景。
热力图更新合并到 200 ms，实时图刷新不会重新计算热力图。
结束时强制刷新最终状态；运行中的旧 Mapping 绘图暂停，避免双重刷新。

硬件无关验证：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_workflow_v2*.py' -v
.venv/Scripts/python.exe scripts/benchmark_workflow_plot.py
```

基准结果和实际渲染预览位于 `.runtime/workflow-v2/`。
Windows 11 / Python 3.14.5 / Matplotlib Agg 的一次实测：

| 场景 | 中位耗时 | P95 |
| --- | ---: | ---: |
| 三幅实时图，每幅两条同源曲线；原始每条 10 万点，显示约 2382 点 | 17.0 ms | 17.5 ms |
| 三幅热力图，每幅 100×100 格 | 27.5 ms | 29.6 ms |

模拟收集 10 万采样约 0.99 s，保留全部采样、待处理显示通知为 1。
这些是渲染基准，不包含真实仪器延迟或 Tk 窗口合成开销；20 FPS 是刷新上限，不是所有布局的实机保证。
自动化测试另外覆盖四种 Tk 布局、公式编辑、跨点隔离、失败时保留部分曲线，以及 Mapping 归档后回看所需的文件。
