#!/usr/bin/env python3
"""
生成「固定 Ids、按 L 从小到大」的 gm/id 查找表。
表结构与原表一致：Vgs, L1, Id, gm, cgs, ro, gm/id, gm/cgs, gmro, Id/W。
每行对应 (L, Vgs)，W 由迭代求得使 Id = Ids，故 Id/W = Ids/W（A/µm，Ids 用 A，W 用 µm）。

输出目录：默认写入 scene2/lookup_tables/fixed_ids/，不覆盖原有 gmid_nmos.csv / gmid_pmos_lvt.csv。

耗时（约）：每个 (L,Vgs) 点需 2～10 次 ngspice .op，单次约 1～5 秒。默认 L 38 点 × Vgs 91 点 ≈ 3458 点，
  总仿真约 1～5 万次，整体约 2～15 小时/表（视机器而定）。可用 --quick 或加大 --l-step/--vgs-step 缩短。

用法（在项目根目录）:
  python scene2/lookup_tables/gen_table_fixed_ids.py --ids 15 --type nmos
  python scene2/lookup_tables/gen_table_fixed_ids.py --ids 15 --type nmos --quick   # 粗网格，约 10～30 分钟
  python scene2/lookup_tables/gen_table_fixed_ids.py --ids 5 --type pmos --out scene2/lookup_tables/fixed_ids/gmid_pmos_5uA.csv

NMCNR 电流比例（I_ref=5µA）：偏置=5µA，第一级对管=10µA，第二级=5µA，第三级 xm23=50µA（10×I_ref）。
  若为 xm23 查表用，建议生成 --ids 50 的 NMOS 表；偏置/小电流管可用 --ids 5。
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time

# 从脚本所在目录推断 scene2 与模型路径（从项目根运行）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCENE2 = os.path.dirname(_SCRIPT_DIR)
_PROJECT_ROOT = os.path.dirname(_SCENE2)
MODEL_LIB = os.path.join(_SCENE2, "models", "sky130.lib.spice")
# 新表输出到固定子目录，不覆盖原 lookup_tables 下的 gmid_nmos.csv / gmid_pmos_lvt.csv
OUTPUT_SUBDIR = "fixed_ids"
FIXED_IDS_OUT_DIR = os.path.join(_SCRIPT_DIR, OUTPUT_SUBDIR)

# 默认扫描范围（与现有表一致）
L_MIN = 0.15
L_MAX = 2.0
L_STEP = 0.05
L_STEP_QUICK = 0.2   # --quick 时 L 步长
# Vgs 范围：NMOS 正，PMOS 负 (start, end, step)
VGS_NMOS = (0.35, 1.25, 0.01)
VGS_PMOS = (-1.25, -0.35, 0.01)
VGS_STEP_QUICK = 0.05  # --quick 时 Vgs 步长
# 迭代求 W 的容差与最大次数
IDS_TOL_REL = 0.002
W_MIN_UM = 0.1
W_MAX_UM = 500.0
MAX_W_ITER = 15


def _make_nmos_cir(l_um, w_um, vgs_v, work_dir):
    """生成单管 NMOS DC 网表，.op 后 print 到 stdout。.lib 用相对项目根的路径，便于 ngspice 解析 lib 内 .include ../cells。"""
    lib_rel = os.path.relpath(MODEL_LIB, _PROJECT_ROOT)
    cir = f"""* Single NMOS .op for fixed-Ids table gen
.option ngbehavior=ltpsa
.lib '{lib_rel}' tt
.TEMP 25

* NMOS: drain @ Vdd, source @ 0, gate @ Vgs
M1 d g 0 0 sky130_fd_pr__nfet_01v8 L={l_um} W={w_um}
Vdd d 0 dc 1.8
Vgs g 0 dc {vgs_v}

.op
.control
run
print @m.M1.sky130_fd_pr__nfet_01v8[id]
print @m.M1.sky130_fd_pr__nfet_01v8[gm]
print @m.M1.sky130_fd_pr__nfet_01v8[cgs]
print @m.M1.sky130_fd_pr__nfet_01v8[gds]
.endc
.end
"""
    cir_path = os.path.join(work_dir, "_one_nmos.cir")
    with open(cir_path, "w") as f:
        f.write(cir)
    return cir_path


def _make_pmos_cir(l_um, w_um, vgs_v, work_dir):
    """生成单管 PMOS DC 网表（LVT），Vds=-0.9 饱和。"""
    lib_rel = os.path.relpath(MODEL_LIB, _PROJECT_ROOT)
    cir = f"""* Single PMOS LVT .op for fixed-Ids table gen
.option ngbehavior=ltpsa
.lib '{lib_rel}' tt
.TEMP 25

* PMOS: source @ 1.8, drain @ 0.9, Vgs 为 gate 相对 source（负值）
M1 drain gate source source sky130_fd_pr__pfet_01v8_lvt L={l_um} W={w_um}
Vsrc source 0 dc 1.8
Vds drain source dc -0.9
Vgs gate source dc {vgs_v}

.op
.control
run
print @m.M1.sky130_fd_pr__pfet_01v8_lvt[id]
print @m.M1.sky130_fd_pr__pfet_01v8_lvt[gm]
print @m.M1.sky130_fd_pr__pfet_01v8_lvt[cgs]
print @m.M1.sky130_fd_pr__pfet_01v8_lvt[gds]
.endc
.end
"""
    cir_path = os.path.join(work_dir, "_one_pmos.cir")
    with open(cir_path, "w") as f:
        f.write(cir)
    return cir_path


def _parse_ngspice_stdout(stdout):
    """从 ngspice 标准输出中解析 4 个数值（id, gm, cgs, gds），按出现顺序取前 4 个。"""
    nums = []
    for line in (stdout or "").splitlines():
        parts = line.strip().split()
        for p in reversed(parts):
            try:
                v = float(p)
                nums.append(v)
                break
            except ValueError:
                continue
    return nums[:4] if len(nums) >= 4 else None


def _run_op_and_parse(work_dir, cir_path, is_pmos):
    """运行 ngspice -b，从 stdout 解析 id, gm, cgs, gds。从项目根运行以便 .lib 内相对路径生效。"""
    try:
        # 从项目根运行，传入 .cir 相对路径，保证 .lib 的 .include ../cells 能找到 scene2/cells
        cwd = _PROJECT_ROOT
        rel_cir = os.path.relpath(cir_path, cwd)
        r = subprocess.run(
            ["ngspice", "-b", rel_cir],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None
    nums = _parse_ngspice_stdout(r.stdout)
    if nums is None:
        return None
    id_a = abs(nums[0])
    gm = abs(nums[1])
    cgs = abs(nums[2])
    gds = abs(nums[3])
    return (id_a, gm, cgs, gds)


def _find_w_for_ids(l_um, vgs_v, ids_a, is_pmos, work_dir):
    """给定 L、Vgs、目标 Ids(A)，迭代求 W(µm)，使 .op 的 Id ≈ Ids。返回 (W_um, id_a, gm, cgs, gds) 或 None。"""
    w_um = 1.0
    make_cir = _make_pmos_cir if is_pmos else _make_nmos_cir
    for _ in range(MAX_W_ITER):
        cir_path = make_cir(l_um, w_um, vgs_v, work_dir)
        res = _run_op_and_parse(work_dir, cir_path, is_pmos)
        if res is None:
            return None
        id_a, gm, cgs, gds = res
        if id_a < 1e-18:
            return None
        err = abs(id_a - ids_a) / ids_a
        if err <= IDS_TOL_REL:
            return (w_um, id_a, gm, cgs, gds)
        w_new = w_um * ids_a / id_a
        w_new = max(W_MIN_UM, min(W_MAX_UM, w_new))
        if abs(w_new - w_um) < 1e-6:
            break
        w_um = w_new
    return (w_um, id_a, gm, cgs, gds)


def _row(L, Vgs, Id, gm, cgs, gds, W_um):
    ro = 1.0 / gds if gds > 1e-18 else 1e12
    gm_id = gm / Id if Id > 1e-18 else 0.0
    gm_cgs = gm / cgs if cgs > 1e-18 else 0.0
    gmro = gm * ro
    id_w = (Id / W_um) if W_um > 0 else 0.0  # A/µm
    return {
        "Vgs": Vgs,
        "L1": L,
        "Id": Id,
        "gm": gm,
        "cgs": cgs,
        "ro": ro,
        "gm/id": gm_id,
        "gm/cgs": gm_cgs,
        "gmro": gmro,
        "Id/W": id_w,
    }


def generate_table(ids_ua, device_type, l_min=L_MIN, l_max=L_MAX, l_step=L_STEP, vgs_step=None, out_path=None):
    ids_a = ids_ua * 1e-6
    is_pmos = device_type.lower() in ("pmos", "pfet", "pmos_lvt")
    if is_pmos:
        vgs_start, vgs_end, default_vgs_step = VGS_PMOS
    else:
        vgs_start, vgs_end, default_vgs_step = VGS_NMOS
    vgs_step = vgs_step if vgs_step is not None else default_vgs_step

    work_dir = _SCRIPT_DIR

    L_list = []
    v = l_min
    while v <= l_max + 1e-9:
        L_list.append(round(v, 3))
        v += l_step

    Vgs_list = []
    v = vgs_start
    while (v <= vgs_end) if vgs_start <= vgs_end else (v >= vgs_end):
        Vgs_list.append(round(v, 4))
        v += vgs_step

    n_total = len(L_list) * len(Vgs_list)
    print(f"扫描点数: L={len(L_list)} × Vgs={len(Vgs_list)} = {n_total}（每点约 2～10 次 ngspice，预计 10 分钟～15 小时）")
    rows = []
    n_done = 0
    t0 = time.time()
    for L in L_list:
        for Vgs in Vgs_list:
            n_done += 1
            # 每 10 点或前 5 点打印，避免 6～199 长时间无输出被误认为卡住
            if n_done <= 5 or n_done % 10 == 0 or n_done == n_total:
                elapsed = time.time() - t0
                print(f"  [{n_done}/{n_total}] L={L} Vgs={Vgs} ... ({elapsed:.0f}s)", flush=True)
            res = _find_w_for_ids(L, Vgs, ids_a, is_pmos, work_dir)
            if res is None:
                continue
            W_um, id_a, gm, cgs, gds = res
            rows.append(_row(L, Vgs, id_a, gm, cgs, gds, W_um))

    if not rows:
        print("未得到任何有效行，请检查模型路径与 Vgs/L 范围。")
        return 0

    fieldnames = ["Vgs", "L1", "Id", "gm", "cgs", "ro", "gm/id", "gm/cgs", "gmro", "Id/W"]
    if out_path:
        out_file = out_path
    else:
        out_file = os.path.join(FIXED_IDS_OUT_DIR, f"gmid_{device_type}_{ids_ua}uA.csv")
    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_file, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"已写入 {len(rows)} 行 -> {out_file}")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="固定 Ids、按 L 从小到大扫，生成 gm/id 查找表")
    ap.add_argument("--ids", type=float, default=15.0, help="固定电流 (µA)，默认 15")
    ap.add_argument("--type", type=str, default="nmos", choices=["nmos", "pmos", "pmos_lvt"],
                    help="器件类型")
    ap.add_argument("--out", type=str, default=None, help="输出 CSV 路径")
    ap.add_argument("--l-min", type=float, default=L_MIN, help="L 最小值 (µm)")
    ap.add_argument("--l-max", type=float, default=L_MAX, help="L 最大值 (µm)")
    ap.add_argument("--l-step", type=float, default=None, help="L 步长 (µm)，未设时默认 0.05，--quick 时为 0.2")
    ap.add_argument("--vgs-step", type=float, default=None, help="Vgs 步长 (V)，未设时默认 0.01，--quick 时为 0.05")
    ap.add_argument("--quick", action="store_true", help="粗网格：L 步长 0.2、Vgs 步长 0.05，约 10～30 分钟/表")
    args = ap.parse_args()

    if not os.path.isfile(MODEL_LIB):
        print(f"未找到模型库: {MODEL_LIB}，请从项目根运行并确认 scene2/models 存在。", file=sys.stderr)
        sys.exit(1)

    l_step = args.l_step if args.l_step is not None else (L_STEP_QUICK if args.quick else L_STEP)
    vgs_step = args.vgs_step if args.vgs_step is not None else (VGS_STEP_QUICK if args.quick else None)

    # 建议从项目根运行，以便 .lib 内相对路径正确
    os.chdir(_PROJECT_ROOT)
    n = generate_table(
        ids_ua=args.ids,
        device_type=args.type,
        l_min=args.l_min,
        l_max=args.l_max,
        l_step=l_step,
        vgs_step=vgs_step,
        out_path=args.out,
    )
    sys.exit(0 if n > 0 else 1)


if __name__ == "__main__":
    main()
