# AutoTest 断点续测

1. 重启软件后，重新连接台体与仪器，并完成 LayoutMap 和 FocusMap 校准。续测不会在启动软件时自动移动台体。
2. 在 AutoTest 的 Execution 区点击 **Resume Session...**，选择原来的 `autotest_session/日期时间` 文件夹。此操作只加载会话，不移动台体或启动测量。Test Point、Device Separate and Approach、Measurement Flow 会显示保存的设置，工作流编辑器会打开供检查。确认无误后关闭编辑器，点击 **Start Loaded Resume**。不要选择其中的 `iv` 或 `workflow_plots` 子文件夹。
3. 软件只跳过从第一个点开始、具有完整 `done` 记录的连续测点；失败点、停止时未完成的点和之后的点会重新测试。恢复后的结果继续写入所选会话文件夹，已完成点的 CSV 和 Plot Panel 记录不覆盖。重测点如已有同名 IV CSV，会写入带序号的新文件。
4. 新建 AutoTest 会话会保存 `autotest_resume.json`，其中包含测量参数、原始 GDS 点列表和逐点完成检查点。续测时使用该测量计划，并按当前 LayoutMap 重新计算台面 X/Y；FocusMap 仍需重新校准。
5. 对升级前没有恢复清单的旧会话，先在 AutoTest 页面恢复原测量流程和点列表，再选择会话文件夹。软件仅依据 Plot Panel 的 `done` 历史跳过连续测点；IV 工作流还要求对应结果 sidecar 的器件名、顺序、GDS 坐标和扫描参数匹配。旧会话至少需要一个可核对的已完成点；无法核对时拒绝续测，以免误跳点。

记录示例：若第 174 点 DevF15 已完成，而移往第 175 点 DevG15 时 X 轴读数超时，恢复后从 DevG15 开始。重新测试前应解决运动读数超时并确认当前校准有效。

针对旧会话 `20260922_212103`，已依据 174 个 IV 结果和 Plot Panel 记录补建 `autotest_resume.json`。原始点阵为 18 行 × 12 列，首个未完成点为 DevG15（175/216）。该旧会话没有保存完整 Z 接近参数；恢复清单中的 Z down、分段上升速度采用旧界面默认值，wobble 与接触 offset 由运行日志复原。更新并重启软件后，选择此会话会先加载保存的设置；点击 **Start Loaded Resume** 时显示 Z 参数确认框。

若远程台体的 SSH/socket 串口在 AutoTest 中断开（例如 Windows 10054），软件会停止当前点、保留已完成检查点，并尝试只恢复通信和读取位置，不自动重复运动。清理完成后会重新加载同一 session；检查台体与探针位置后点击 **Start Loaded Resume**。
