# scene1 与 agents 网表、偏置对比

scene1 现仅支持**套筒式**（sys0_sleeve）拓扑；下表描述套筒式主 OTA 与 agents 的一致性。

## 结论：主 OTA 部分一致

scene1 的**套筒式 OTA 网表**和**偏置公式**与 agents 中主电路（mosfet_dict + voltage_source_dict）**一致**。agents 在此外还有 SL（slew-rate）模块，scene1 也支持 SL 模块（套筒式 9 管 + SL 电路）。

---

## 1. 电路网表（主 OTA）

| 项目 | agents (prompt/sys0_prompt.txt) | scene1 (prompt/sys0_sleeve.txt，套筒式) |
|------|---------------------------------|----------------------------------|
| 子电路 | .subckt AMP Vinp Vinn VDD VSS Vout | 相同 |
| XM1 | net2 Vinp net3 net3 nmos | 相同 |
| XM2 | net1 Vinn net3 net3 nmos | 相同 |
| XM3 | net4 vbn2 net2 net2 nmos | 相同 |
| XM4 | Vout vbn2 net1 net1 nmos | 相同 |
| XM5 | net4 vbp net6 net6 pmos | 相同 |
| XM6 | Vout vbp net5 net5 pmos | 相同 |
| XM7 | net6 net4 VDD VDD pmos | 相同 |
| XM8 | net5 net4 VDD VDD pmos | 相同 |
| XM9 | net3 vbn1 VSS VSS nmos | 相同 |
| 电压源 | VBP vbp VSS, VBN2 vbn2 VSS, VBN1 vbn1 VSS | 相同 |

生成网表时：
- 模型：nmos → sky130_fd_pr__nfet_01v8，pmos → sky130_fd_pr__pfet_01v8_lvt（两边相同）
- W>100 时：W/10, m=10（两边相同）
- 电压源输出：`name pos neg dc={dc}`（两边相同）

---

## 2. 偏置（update_vb_result）

公式**完全一致**：

- **VBN1** = Vgs9（保留 2 位小数）
- **VBN2** = VCM - Vgs1 + (VDD + Vgs7 - VCM + Vgs1)/2 + Vgs3
- **VBP**  = VDD + Vgs7/2 + Vgs5

Vgs 来源：agents 来自 update_csv_result（查表），scene1 来自 apply_lookup_to_mosfet_dict（查表），均用 L、gm/id 查表得到 Vgs、Id/W。  
两边实现已对齐：scene1 的 update_vb_result 与 agents 逻辑一致，不再对 Vgs 做额外过滤。

---

## 3. 电流分配（update_ids_result）

- XM9：ids = Itail  
- 其余 XM1–XM8：ids = Itail/2  

两边逻辑相同。

---

## 4. agents 多出的部分

agents 还解析并生成 **SL 模块**（sys0_1_prompt.txt）：XSLM10、XSLM11、XSLM12、VSLBP1，与主 OTA 一起写进同一 .subckt AMP。scene1 仅实现主 OTA（XM1–XM9 + VBP/VBN1/VBN2），不包含 SL。

如需与 agents 完全同一网表（含 SL），需在 scene1 中增加对 sys0_1 的解析与生成逻辑。
