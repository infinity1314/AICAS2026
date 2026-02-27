# Agent2 饱和区调整问题分析

## 1. 流程概览

- **入口**：`main.py` 中 `agent2.invoke(specs, topology, simulate_fn=simulate_fn)`，若提供 `simulate_fn` 则先做饱和迭代，再返回约束与 `_initial_sizing`。
- **饱和迭代**（agent2.py 第 214–232 行）：用约束中点作为初始 sizing，循环调用 `simulate_fn(sizing)` 得到 `device_op`；若存在未饱和管，则用 **规则** `_adjust_for_linear(sizing, constraints, device_op)` 得到下一轮 sizing，直到全部饱和或达到 `max_iter=12`。
- **饱和判据**（utils.py `read_operating_point`）：从 `Op_1.txt` 读每管 Vds、Vgs、Vth；NMOS 饱和判为 `Vds >= Vgs-Vth`，PMOS 判为 `Vds <= Vgs-Vth`（即用 `Vgs-Vth` 近似 Vdsat）。

---

## 2. 规则调整 `_adjust_for_linear` 的问题

### 2.1 策略与实现

对 **每个** 在 `device_op` 中标记为未饱和的管子：

- **L**：`L_new = min(L_max, L_cur*1.2, L_cur+0.12)`，再 clamp 到 `[L_min, L_max]`
- **gm_id**：`g_new = max(g_min, g_cur - 1.0)`（只减不加，无上界约束）

意图：增大 L、减小 gm_id 一般会增大有效 Vdsat、提高 ro，使管子更容易进入饱和。

### 2.2 具体问题

| 问题 | 说明 |
|------|------|
| **gm_id 无上界** | 只做 `g_new = max(g_min, g_cur - 1.0)`。若某管已接近 `g_min`，一两轮就贴到下限，若仍不饱和则无法再减 gm_id，只能靠 L 继续增，容易与 L 上限冲突或步长不足。 |
| **步长固定、与角色无关** | 所有管子统一 L×1.2 / +0.12、gm_id -1.0。共源共栅下臂（XM7/XM8）Vds 常较小，可能需更大步长或只调 L；输入对、尾管对饱和敏感度不同，统一步长易导致有的过调、有的多轮仍不饱和。 |
| **多管同时调、耦合未考虑** | 多管未饱和时，每管都按同一规则改。例如负载管 L 增大后电流/节点电压变化，可能使 cascode 的 Vds 重新分配，出现“调完 A 饱和了、B 又变线性”或反复震荡。 |
| **收敛判据过严** | `next_sizing == sizing` 即 break（第 226–227 行）。若未饱和管已到 L_max 且 gm_id=g_min，下一轮 sizing 不变，会直接退出，**部分管仍可能未饱和**。 |
| **未区分“差一点”和“差很多”** | 离饱和只差一点时，L×1.2 和 gm_id-1.0 可能过猛；离饱和很远时，固定步长可能导致 12 轮内仍不饱和。没有根据 Vds 与 Vdsat 的差距做自适应步长。 |

### 2.3 为什么当前规则在实践中仍常有效

尽管有上述局限，**“未饱和管：L 增大 + gm_id 减小”** 在套筒 OTA 里往往能把管子拉进饱和，原因主要来自器件和电路两方面：

- **L 增大 → 更容易饱和（主因）**  
  输出电阻近似有 ro ∝ L（沟道越长，Early 效应下 ro 越大）。在叠层结构里（cascode、负载+共源共栅），各管串联分压：**ro 大的管子分到的 Vds 大**。对未饱和的那只管把 L 增大 → 其 ro 增大 → 该支路中它分到的 Vds 增加 → 更容易满足 Vds ≥ Vdsat，从而进入饱和。所以 **L↑ 是通过改变支路电压分配来提升饱和裕度**，这是规则有效的主要机制。

- **gm_id 减小 → 配合 L 改善工作点**  
  gm_id 减小后，在 gmid 表里会对应新的 W（idW 变化），同一 Id 下整管的宽长比和偏置点会变，从而改变该支路的 DC 解。实践中常见效果包括：  
  - 支路阻抗/电流分配变化，使该管所在节点的 Vds 有所增加；或  
  - 从弱反型往强反型靠拢，偏置更稳定，和 L 增大一起把管子推过饱和边界。  
  因此 **gm_id↓ 更多是配合 L↑，通过改变电路 DC 解和电压分配来起作用**，而不是单纯把“单管 Vdsat”变小。

- **套筒结构的典型“短板”**  
  最容易不饱和的往往是 **共源共栅下臂**（XM7/XM8）：它们在上管下面，Vds 本来就小。对它们做 L↑、gm_id↓，正好是“让这一段的 ro 变大、工作点略调”，使这段分到更多压降，所以规则在多数初值下几轮内就能把这类管调进饱和。  
  输入对、负载、尾管若未饱和，通常离饱和边界不远，同样的 L/gm_id 微调也常能拉过去。

- **小结**  
  规则有效，主要是因为 **L↑ 直接提高了未饱和管的 ro，在串联分压里给它更多 Vds**；gm_id↓ 则通过改变 W 和 DC 解配合这一过程。因此即便步长固定、不按角色细分，在“离饱和不太远”的常见情况下仍能收敛；只有在边界情况（已贴 g_min/L_max、多管强耦合、或工艺/模型使 Vdsat 与 (Vgs−Vth) 偏差大）时，才会暴露出 2.2 节中的那些问题。

### 2.4 LLM 分支未接入

- `_adjust_for_linear_llm` 已实现（第 124–173 行），依赖 `sys2_saturation.txt` / `human2_saturation.txt`，且会 clip 到约束内、失败时回退规则。
- 但在 **invoke 的饱和循环里只调用了 `_adjust_for_linear`**（第 225 行），**从未调用 `_adjust_for_linear_llm`**，因此当前所有饱和迭代都是规则调整，LLM 饱和逻辑未被使用。

---

## 3. 饱和判据（utils.read_operating_point）的问题

| 问题 | 说明 |
|------|------|
| **Vdsat 近似** | 用 `Vgs - Vth` 近似饱和条件。短沟道或高 gm_id（弱反型）时，实际 Vdsat 与 (Vgs-Vth) 差异较大，可能误判饱和/线性。 |
| **Vgs-Vth=0** | 代码用 `(vgs - vth) != 0` 分支，相等时 NMOS 判 `vds>=0`、PMOS 判 `vds<=0`。管子接近关断或亚阈时，易产生边界误判。 |
| **无裕度** | 判据是严格不等式（如 NMOS `vds >= vgs-vth`），没有“饱和裕度”（如要求 Vds-Vdsat > 50mV），可能把临界区判成饱和，后续 AC 行为不稳定。 |

---

## 4. 空 device_op 导致误判“全部饱和”

- `device_op` 来自 `simulate_fn` → `run_sizing_and_get_op` → `read_operating_point`。若 `Op_1.txt` 缺失或解析失败，`read_operating_point` 返回 `{}`。
- 饱和循环中：`all_sat = all(info.get("saturation") for info in device_op.values())`。**对空字典，`all([])` 为 True**，因此会认为“全部饱和”并立即退出，**未做任何饱和迭代**，且可能掩盖仿真/解析错误。

---

## 5. 改进建议（简要）

1. **空 device_op**：若 `not device_op`，不应视为全部饱和；应 break 并打日志，或跳过饱和迭代、用初始 sizing 进入 Agent3。
2. **规则步长**：  
   - gm_id 建议同时限制上界：`g_new = max(g_min, min(g_max, g_cur - 1.0))`。  
   - 可考虑按“离饱和的差距”（若能从 Op 得到 Vds、Vdsat）做自适应步长；或至少对 cascode 下臂用更大 L 步长、更小 gm_id 步长。
3. **收敛退出**：当未饱和管已到约束边界（L=L_max 且 gm_id=g_min）仍不饱和时，建议打明确日志并记录“部分管可能仍在线性区”，而不是静默退出。
4. **可选启用 LLM**：在饱和循环中将 `_adjust_for_linear` 替换为 `_adjust_for_linear_llm`（或通过配置选择），以便利用角色与工作点做差异化调整。
5. **饱和裕度**：若仿真能提供 Vdsat 或更精确的饱和标志，可在 `read_operating_point` 中引入裕度（如 Vds - Vdsat > margin）再判 saturation，减少临界误判。

---

## 6. 相关代码位置

| 内容 | 文件与位置 |
|------|------------|
| 饱和迭代循环、规则调整调用 | `scene1/agents/agent2.py` 第 214–232、225 行 |
| 规则 L/gm_id 更新逻辑 | `scene1/agents/agent2.py` 第 102–121 行 `_adjust_for_linear` |
| LLM 饱和调整（未接入） | `scene1/agents/agent2.py` 第 124–173 行 `_adjust_for_linear_llm` |
| 饱和判据 Vds/Vgs/Vth | `scene1/agents/utils.py` 第 461–527 行 `read_operating_point` |
| simulate_fn、Agent2 调用 | `scene1/main.py` 第 193–202 行 |
