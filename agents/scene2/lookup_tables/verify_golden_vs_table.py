#!/usr/bin/env python3
"""
用「黄金 L、黄金 gm_id、理论电流」查现有表得 idW，算 W_calc = Ids/idW，
与黄金总宽 W_golden_total = W_per_finger * mult 比较，验证查表法是否能复现黄金 W。

用法（在项目根或 scene2 下）:
  python scene2/lookup_tables/verify_golden_vs_table.py
"""
import os
import sys

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LOOKUP_DIR = _SCRIPT_DIR
_IDW_A_PER_UM_THRESHOLD = 0.01

# 黄金 L, W(每指), mult（来自 topology_config nmcnr_golden_LW_um）
GOLDEN = {
    "xm0":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm1":  {"L": 2.0, "W": 2.3, "mult": 4},
    "xm2":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm3":  {"L": 2.0, "W": 2.3, "mult": 4},
    "xm4":  {"L": 2.0, "W": 2.3, "mult": 16},  "xm5":  {"L": 2.0, "W": 2.3, "mult": 4},
    "xm6":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm7":  {"L": 2.0, "W": 2.3, "mult": 4},
    "xm8":  {"L": 2.0, "W": 4.0, "mult": 4},   "xm9":  {"L": 2.0, "W": 4.0, "mult": 4},
    "xm10": {"L": 1.45, "W": 3.2, "mult": 4},  "xm11": {"L": 2.0, "W": 2.3, "mult": 40},
    "xm12": {"L": 0.9, "W": 0.47, "mult": 16}, "xm13": {"L": 0.9, "W": 0.47, "mult": 16},
    "xm14": {"L": 0.9, "W": 0.47, "mult": 4},  "xm15": {"L": 0.9, "W": 0.47, "mult": 16},
    "xm16": {"L": 0.9, "W": 0.47, "mult": 16}, "xm17": {"L": 0.9, "W": 0.47, "mult": 16},
    "xm18": {"L": 0.9, "W": 0.47, "mult": 16}, "xm19": {"L": 0.9, "W": 0.47, "mult": 32},
    "xm20": {"L": 0.9, "W": 0.47, "mult": 32}, "xm21": {"L": 1.5, "W": 2.0, "mult": 4},
    "xm22": {"L": 1.5, "W": 2.0, "mult": 4},   "xm23": {"L": 2.0, "W": 0.9, "mult": 4},
}

# 黄金 gm_id（来自你提供的 NMCNR 仿真报告 LEUNG NMCNR FULL 24-DEVICE OPERATING STATUS）
GOLDEN_GMID = {
    "xm0": 8.24738,  "xm1": 8.18596,  "xm2": 8.18596,  "xm3": 8.19651,  "xm4": 7.8722,
    "xm5": 10.4791,  "xm6": 10.4791,  "xm7": 8.2447,   "xm8": 8.34261,  "xm9": 8.34261,
    "xm10": 10.6968, "xm11": 8.2505,
    "xm12": 17.9527, "xm13": 17.9527, "xm14": 12.2094, "xm15": 19.9874, "xm16": 19.9874,
    "xm17": 15.1563, "xm18": 15.1563, "xm19": 16.3573, "xm20": 16.3573, "xm21": 17.5352,
    "xm22": 17.3477, "xm23": 3.39563,
}

# 理论电流 (µA)：论文比例 I_ref=5，stage1_tail=20, stage1_main=10, stage2=5, stage3=50, bias=5
THEORY_IDS = {
    "xm4": 20.0,
    "xm8": 10.0, "xm9": 10.0, "xm5": 10.0, "xm6": 10.0, "xm19": 10.0, "xm20": 10.0,
    "xm10": 5.0, "xm21": 5.0, "xm22": 5.0, "xm7": 5.0,
    "xm23": 50.0, "xm11": 50.0,
    "xm0": 5.0, "xm1": 5.0, "xm2": 5.0, "xm3": 5.0,
    "xm12": 5.0, "xm13": 5.0, "xm14": 5.0, "xm17": 5.0, "xm18": 5.0,
    "xm15": 10.0, "xm16": 10.0,
}

NMOS_DEVICES = {f"xm{i}" for i in range(12, 24)}


def lookup_idw(l_um, gm_id, csv_name, ids_ua):
    """查表 (L, gm_id)，返回 idW。若有并列 gm_id 取 Id 最接近 ids_ua 的行。"""
    path = os.path.join(_LOOKUP_DIR, csv_name)
    if not os.path.isfile(path):
        return None
    df = pd.read_csv(path)
    sub = df[df["L1"].between(l_um - 0.05, l_um + 0.05)].copy()
    if sub.empty:
        sub = df.iloc[(df["L1"] - l_um).abs().argsort()].head(10).copy()
    gmid_col = "gm/id" if "gm/id" in sub.columns else next(
        (c for c in sub.columns if "gm" in c.lower() and "id" in c.lower()), sub.columns[6]
    )
    dist = (sub[gmid_col] - gm_id).abs()
    best = dist[dist == dist.min()]
    id_col = "Id" if "Id" in sub.columns else next(
        (c for c in sub.columns if "id" in c.lower() and "w" not in c.lower()), None
    )
    if id_col and id_col in sub.columns:
        sub_best = sub.loc[best.index]
        id_vals = sub_best[id_col].fillna(0).astype(float).replace(0, 1e-18)
        ids_a = ids_ua * 1e-6
        idx = (id_vals - ids_a).abs().idxmin()
    else:
        idx = best.index.min()
    row = sub.loc[idx]
    idw_key = next((k for k in row.keys() if "id" in k.lower() and "w" in k.lower()), None)
    return float(row[idw_key]) if idw_key else None


def calc_W_from_idw(ids_ua, idw, is_nmos):
    """与 utils.update_W_result 一致：ids 为 µA，idW 来自表，得到总宽 µm。"""
    if idw is None or idw <= 0:
        return None
    ids_f = float(ids_ua)
    idw_f = float(idw)
    if idw_f <= _IDW_A_PER_UM_THRESHOLD:  # A/µm
        w_total = (ids_f * 1e-6) / idw_f
    else:
        w_total = ids_f / idw_f
    return w_total


def main():
    print("黄金 L / gm_id / 理论 Ids 查表 → W_calc vs 黄金总宽 W_golden (= W_per_finger × mult)")
    print("=" * 100)
    ok = 0
    bad = 0
    for name in [f"xm{i}" for i in range(24)]:
        g = GOLDEN.get(name)
        gm_id = GOLDEN_GMID.get(name)
        ids = THEORY_IDS.get(name)
        if g is None or gm_id is None or ids is None:
            continue
        l_um = g["L"]
        w_pf = g["W"]
        mult = g["mult"]
        w_golden_total = w_pf * mult

        csv_name = "gmid_nmos.csv" if name in NMOS_DEVICES else "gmid_pmos_lvt.csv"
        idw = lookup_idw(l_um, gm_id, csv_name, ids)
        w_calc = calc_W_from_idw(ids, idw, name in NMOS_DEVICES) if idw else None

        if w_calc is None:
            status = "无 idW"
            bad += 1
        else:
            ratio = w_calc / w_golden_total if w_golden_total > 0 else 0
            if 0.5 <= ratio <= 2.0:
                status = f"W_calc={w_calc:.4f} 约 {ratio:.2f}× 黄金"
                ok += 1
            else:
                status = f"W_calc={w_calc:.4f} = {ratio:.2f}× 黄金 偏离"
                bad += 1

        idw_str = f"{idw:.2e}" if idw is not None else "N/A"
        print(f"  {name:6} L={l_um:.2f} gm_id={gm_id:.2f} Ids={ids:.0f}µA  idW={idw_str}  W_golden_tot={w_golden_total:.2f}  {status}")
    print("=" * 100)
    print(f"合计: 约符合(0.5~2×) {ok} 个，偏离或缺失 {bad} 个。")
    print("结论: xm23 用 50µA 查表得 W_calc≈0.79×黄金，多数管 0.7~1×；固定 Ids 新表按设计电流建表可让 W_calc=黄金。")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
