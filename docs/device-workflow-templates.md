# 器件测试模板

在 AutoTest 或 Wire-bond Mapping 的 **Config Measure Flow → Template** 中加载。
Template 按 FET、PD、MR、Cap、General 分类，FET 下再按 B1500 / HP6614C 分组。
分类节点用于展开，叶子节点可加载或复制到个人目录。名称不包含版本号；文件统一为 version 2，旧版/无版本文件不再导入，旧别名已移除，个人文件不会自动改写。
每个模板都有 Start / End、统一 Plot Panel；数据线不触发额外测量。表中布局按行×列表示。

## 测试流程与测量点

器件类型与接触方式分开：FET、PD、MR、Cap 和 General 模板共用 AutoTest / Mapping 执行器。
Probe 由器件列表生成点并完成探针接触，Bonding 由位置列表生成点、保持既有接线；每到一个点执行同一套 workflow。
卡片内不包含探针落针或 WobbTest，也不按 Bonding 重复定义模板。各点的曲线、特征和分支上下文独立。

## 模板清单

| 分类 / 模板名称 | 执行与输出 | Panel |
|---|---|---|
| FET / B1500 / Transfer | Id–Vg、Vth、on/off ratio、SS | 2×2 |
| FET / B1500 / Double Sweep | 双向 Id–Vg；同一参数提取器输出 Vth、on/off、SS | 2×2 |
| FET / B1500 / Output Family | 不同 Vg 下 Id–Vd 曲线族、全扫描区间拟合电阻 | 1×2 |
| FET / B1500 / Screen and Characterize | 转移测试 → on/off 筛查 → 合格才执行输出曲线；不合格跳过 | 2×3 |
| FET / HP6614C / Screen and Characterize | HP6614C 栅压源 + Keithley2450 漏极测量，同上 | 2×3 |
| PD / Dark Light Current | 关光暗态采样 → 开光亮态采样 → 关光 → 光电流阈值分支；两条实时图和四张特征图 | 2×3 |
| PD / Dark Light IV | 分别采集暗态、亮态 I–V；各自拟合电导 | 2×2 |
| PD / Responsivity and EQE | 暗/亮电流差、明暗比、响应度、EQE；需填写入射光功率和波长 | 2×3 |
| PD / Pulse Response | DP100 五周期脉冲 + I–t；全轨迹幅度、积分电荷、标准差 | 2×2 |
| PD / Calibrated Power and Width | 显式 Laser I–V 校正 → 功率梯度 → 脉宽梯度；两路曲线、幅度及斜率 | 2×3 |
| General / Continuity and Resistance | 小电流扫描，实测 V–I 拟合两端电阻，电压保护阈值和电阻阈值共同筛查 | 1×2 |
| General / Bipolar Leakage | 正负偏压下分别采样，以两侧绝对平均漏电流的最大值筛查 | 2×2 |
| General / Current Stability | 固定偏压连续采样；平均电流、相对标准差及随时间的拟合漂移 | 2×2 |
| MR / Bipolar Switching IV | 0 → +V → −V → 0 的双极 I–V、峰值电流 | 1×2 |
| MR / Read SET Read RESET Read | 初始读阻 → SET → 读阻 → RESET → 读阻，阻值窗口判断 | 2×3 |
| Cap / DC Leakage and Insulation | 偏压等待后采样、直流漏电和表观绝缘电阻 | 2×2 |
| Cap / Bipolar Leakage | 正负偏压的漏电筛查 | 2×2 |

PD / TEMT6000 下保留固定光照和功率/脉宽序列，均已接入 Start / End 和 Plot Panel。
General / Examples 包含曲线与热力图示例、公式分支示例。Spatial / Virtual Mask 保留位置绑定图案照明；这是 Mapping 专用的空间配置，不属于器件电学模板。

## 运行前填写

- 确认仪器资源、接线端子和 SMU 分配。示例 FET 使用 B1500 SMU3/SMU4，Vg 0–2 V、Vd 0.1/0.5 V；输出扫描 Vd 0–1 V。它们是可编辑起点，不代表特定器件额定范围。
- 筛查阈值位于 `acceptance` 公式卡的 constants：FET on/off 默认 ≥100；光电流绝对值默认 ≥10 nA；通用导通电阻 ≤100 Ω、双极漏电 ≤10 nA、相对电流变化 ≤5%。True / False 接到独立 PASS / FAIL 暂停卡，均通往 End。FET True 先执行输出测试。
- 通用导通模板源电流 −100…100 µA、步长 10 µA、电压限制 0.1 V。`voltage_guard` 默认 0.095 V，是实测电压边界筛查，并非仪器 compliance 状态读取；修改电压限制时同步调整。测得的是线缆、接点和器件路径的**两端总电阻**。
- 双极漏电默认 ±0.1 V；只在需要检验绝缘的两点之间连接。导通测试与绝缘测试是独立模板，需要相应接线。这里不包含键合机拉力/剪切测试或自动切换接线。
- MR 示例 SET / RESET 使用 +1 / −1 V 的固定偏压驻留（SMU 通信与积分决定时序，不是精确定宽脉冲），读电压 0.1 V，电流限制 100 µA。请按器件修改写入电压、极性、驻留时间和限制。窗口为 RESET 后电阻 / SET 后电阻，默认 ≥2；不自动假定器件已经成功翻转。
- Cap 示例偏压 0.1 V，等待 1 s 后采样 50 次，电流限制 10 µA。应按器件调整等待时间至稳定漏电区间。输出电阻使用实测平均电压 / 平均电流；零电流时无效。现有代码没有 LCR / C–V 执行器，模板不输出电容量、损耗角或 ESR。

## 特征含义

光电流为同一点的 `I_light - I_dark`，保留符号；明暗比为 `abs(I_light)/abs(I_dark)`。暗电流为零时比值无效，光电流仍可使用；缺少任一测量时不会借用上一点数据。

响应度为 `abs(I_light-I_dark)/incident_power_w`，功率必须是在器件有效区域实际入射的光功率（W）。默认功率为 0，明确留空结果，填写正值后才输出响应度；负值同样无效。EQE 为 `R × 1239.841984 / wavelength_nm × 100%`，默认波长 650 nm，需要改为实际波长。

DP100 Laser I–V 校正只得到电流、电压及**电驱动功率**关系；梯度模板的 A/mW 不是经光功率计校准的光响应度，不会自动用它填充 incident_power_w。

脉冲模板电荷是按实际时间戳积分的总电荷，包含暗态基线；标准差包含脉冲信号，不是暗噪声密度。当前模板不声称提供尚未实现的上升/下降时间。FET 双向模板不额外输出迟滞窗口；现有参数提取对采集曲线使用既有算法。

所有曲线默认线性坐标，避免负电流被默认对数显示屏蔽。可逐格修改显示范围/坐标。FET 输出电阻拟合覆盖全扫描区间；需要局部输出电阻时，可自行接入公式卡 `1/slope(segment(signal, start, stop))`，segment 使用采集序号切片。

分支不合格仍是一次完成的 workflow；PASS/FAIL 与采集失败不同。无效公式默认走 False，相关热力图留空；修改卡片 on_invalid 为 stop 可中止。FET 筛查数值图用 1/0 表示通过/未通过，缺失值仍留空。

## 验证

新增测试覆盖分类树加载、无旧版别名、全部器件模板在 Probe/Bond 两种上下文下的模拟执行和结果一致性、图校验、保存重载、FET 分支、暗亮态隔离、响应度功率边界、实际时间积分、校正先后顺序、电流源横轴、Bonding 电压保护与筛查。测试使用模拟数据和仪器；没有连接实际器件执行扫描。
