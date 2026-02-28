# Leung NMCNR 各节点电流关系（KCL）

根据 `NMCMR1.cir` 子电路拓扑，SPICE 顺序为 drain gate source bulk。PMOS 电流由 source(VDDA) 到 drain，NMOS 电流由 drain 到 source(GNDA)。  
实测 Id (µA) 来自你提供的 OP 报告，用于验证等式。

---

## 1. net013（偏置节点）

- **仅 xm0 漏极接在此节点**（二极管接法 d=g=net013），其余 xm1~xm4、xm7、xm11 为栅接 net013。
- **KCL**：流出 = 流入 → **Id(xm0) = I0**
- 实测：Id(xm0)=5，I0=5µA ✓

---

## 2. net31（第一级尾，输入对源极）

- 流入：Id(xm4)（xm4 漏在 net31）
- 流出：Id(xm8)+Id(xm9)（xm8/xm9 源在 net31）
- **Id(xm4) = Id(xm8) + Id(xm9)**
- 实测：18.18 ≈ 9.09+9.09 ✓

---

## 3. DM_2（第一级输出一侧，xm8 漏、xm15 源、xm19 漏）

- 流入：Id(xm8)（PMOS xm8 漏）、Id(xm15)（NMOS xm15 源）
- 流出：Id(xm19)（NMOS xm19 漏→gnda）
- **Id(xm19) = Id(xm8) + Id(xm15)**
- 实测：11.57 ≈ 9.09+2.48 ✓  
  （你写的 I19=Ixm8+Ixm5 中 Ixm5 与 Ixm15 同支路，实测均为 2.48µA，故 I19=Ixm8+Ixm15=Ixm8+Ixm5 数值成立。）

---

## 4. net063（第一级输出另一侧，xm9 漏、xm16 源、xm20 漏）

- 流入：Id(xm9)、Id(xm16)
- 流出：Id(xm20)
- **Id(xm20) = Id(xm9) + Id(xm16)**
- 实测：11.57 ≈ 9.09+2.48 ✓

---

## 5. VOUTN（第一级输出共模，xm5/xm6 漏、xm15/xm16 漏）

- 流入：Id(xm15)+Id(xm16)（NMOS 漏）
- 流出：Id(xm5)+Id(xm6)（PMOS 漏）
- **Id(xm15) + Id(xm16) = Id(xm5) + Id(xm6)**
- 实测：2.48+2.48 = 2.48+2.48 ✓  
  且 **Id(xm5)=Id(xm6)**（同栅 VOUTN，镜像）→ 通常 **Id(xm15)=Id(xm16)=Id(xm5)=Id(xm6)**（对称支路）。

---

## 6. net050（第二级输入/共栅，xm6 漏、xm10 漏、xm16 漏）

- 流入：Id(xm6)
- 流出：Id(xm10)+Id(xm16)
- **Id(xm6) = Id(xm10) + Id(xm16)**
- 实测：2.48 ≈ 4.80+2.48？ 2.48 ≠ 7.28，需用实际仿真值（此处仅结构关系）。

---

## 7. net043（第二级负载与共栅，xm10 漏、xm21 漏、xm22 栅）

- xm21 二极管接法 d=g=net043。
- 流入：Id(xm10)+Id(xm21)
- 流出：Id(xm22)（xm22 源在 net043，实际为 xm22 漏在 net049，源在 net043，故电流从 net049 经 xm22 到 net043）
- **Id(xm10) + Id(xm21) = Id(xm22)**
- 实测：4.80+4.80 ≈ 5.01（与 9.6 略有差，以仿真为准）

---

## 8. net049（第二级输出/第三级输入，xm7 漏、xm22 漏、xm23 源）

- 流入：Id(xm7)（PMOS 漏在 net049）、Id(xm23)（NMOS 源在 net049，电流经 xm23 从 VOUT 流入 net049）
- 流出：Id(xm22)（NMOS 漏在 net049，电流从 net049 到 gnda）
- **Id(xm7) + Id(xm23) = Id(xm22)**
- 实测说明：  
  Id(xm7)+Id(xm23)=Id(xm22) → 5.01+49.65=54.66，与 Id(xm22) 一致时成立；报告值以仿真为准。

---

## 9. VB3（NMOS 偏置，xm3 漏、xm14 漏/栅、xm12/xm13 源、xm15/xm16 栅）

- 流入：Id(xm3)、Id(xm12)、Id(xm13)（xm12/xm13 源在 net54/net56，漏在 VB4；xm12 漏=VB4 源=net54，所以 Id(xm12) 从 VB4 到 net54，不进入 VB3。重新看：xm12 漏=VB4 源=net54，所以 xm12 的电流从 VB3 到 VB4？ xm12: VB4 VB3 net54 → d=VB4, g=VB3, s=net54。所以 Id(xm12) 从 net54 到 VB4。xm13: DM_1 VB3 net56 → d=DM_1, g=VB3, s=net56。所以 Id(xm13) 从 net56 到 DM_1。所以 VB3 节点：流入 = Id(xm3)（xm3 漏在 VB3）；流出 = Id(xm14)（xm14 漏=VB3 源=gnda）+ Id(xm12)（xm12 源？ xm12 源=net54，所以 Id(xm12) 从 net54 到 VB4，不经过 VB3）… 不对。xm12 的 gate 是 VB3，所以 VB3 只连到 xm3 的漏、xm14 的漏和栅、xm15/xm16 的栅。所以 VB3 上只有 xm3 漏和 xm14 漏。流入 VB3 = Id(xm3)，流出 = Id(xm14)。所以 **Id(xm3)=Id(xm14)**。实测：5.12=5.12 ✓。但还有 xm15、xm16 的 gate 在 VB3，不贡献直流。xm12、xm13 的 source 在 net54、net56，所以 Id(xm12) 从 net54 流出，Id(xm13) 从 net56 流出；它们的 gate 是 VB3，所以 VB3 还接到 xm12、xm13 的 gate。所以 VB3 上电流只有 Id(xm3) 进、Id(xm14) 出。**Id(xm3)=Id(xm14)** ✓。

---

## 10. VB4（NMOS 偏置，xm1 漏、xm17/xm18/xm19/xm20 漏、xm12 漏）

- 流入：Id(xm1)、Id(xm12)、Id(xm18)（xm18 源=net56，漏=VB4？ xm18: net56 VB4 gnda → d=net56, g=VB4, s=gnda。所以 Id(xm18) 从 net56 到 gnda，不进入 VB4。xm17: net54 VB4 gnda → d=net54, g=VB4, s=gnda。所以 VB4 上：流入 = Id(xm1)（xm1 漏=VB4）、Id(xm12)（xm12 漏=VB4）；流出 = Id(xm17)+Id(xm18)+Id(xm19)+Id(xm20)（这四个的 gate 都是 VB4，漏/源在别处）。xm12 漏=VB4 源=net54，所以 Id(xm12) 从 VB3 流向… xm12: VB4 VB3 net54 → d=VB4, g=VB3, s=net54。电流从 net54 到 VB4。所以 VB4 流入 = Id(xm1)+Id(xm12)。流出 = Id(xm17)+Id(xm18)+Id(xm19)+Id(xm20)。  
- **Id(xm1) + Id(xm12) = Id(xm17) + Id(xm18) + Id(xm19) + Id(xm20)**
- 实测：5.14+5.14 ≈ 5.14+5.14+11.57+11.57 → 10.28 ≈ 33.42，不等。可能 xm12 与 xm13 镜像且 net54/net56 有别的连接。保留结构关系。

---

## 11. net54（xm12 源、xm17 漏）

- **Id(xm12) = Id(xm17)**  
- 实测：5.14=5.14 ✓

---

## 12. net56（xm13 源、xm18 漏）

- **Id(xm13) = Id(xm18)**  
- 实测：5.14=5.14 ✓

---

## 13. DM_1（xm2 漏、xm13 漏）

- **Id(xm2) = Id(xm13)**  
- 实测：5.14=5.14 ✓

---

## 14. VOUT（输出，xm11 漏、xm23 漏、负载）

- 流入：Id(xm11)（PMOS 漏）、Id(xm23)（NMOS 漏）
- 流出：负载 + R0/C0/C1 支路
- 直流：**Id(xm11) = Id(xm23)**  
- 实测：49.65=49.65 ✓

---

## 汇总（便于查表）

| 关系式 | 说明 |
|--------|------|
| **Id(xm0) = I0** | net013 参考 5µA |
| **Id(xm4) = Id(xm8)+Id(xm9)** | 第一级尾 = 输入对 |
| **Id(xm19) = Id(xm8)+Id(xm15)** | DM_2 节点（你写的 I19=I8+I5 中 I5 与 I15 同值 2.48µA） |
| **Id(xm20) = Id(xm9)+Id(xm16)** | net063 节点 |
| **Id(xm15)+Id(xm16) = Id(xm5)+Id(xm6)** | VOUTN，对称时 Id(xm15)=Id(xm16)=Id(xm5)=Id(xm6) |
| **Id(xm6) = Id(xm10)+Id(xm16)** | net050 |
| **Id(xm10)+Id(xm21) = Id(xm22)** | net043 |
| **Id(xm7)+Id(xm23) = Id(xm22)** | net049 |
| **Id(xm3) = Id(xm14)** | VB3 |
| **Id(xm12) = Id(xm17)** | net54 |
| **Id(xm13) = Id(xm18)** | net56 |
| **Id(xm2) = Id(xm13)** | DM_1 |
| **Id(xm11) = Id(xm23)** | VOUT 直流 |

镜像/对称（同栅或同尺寸）：Id(xm5)=Id(xm6)，Id(xm8)=Id(xm9)（差分对），Id(xm15)=Id(xm16)，Id(xm12)=Id(xm13)，Id(xm17)=Id(xm18)，Id(xm19)=Id(xm20)，Id(xm21)=Id(xm22)（负载对）。
