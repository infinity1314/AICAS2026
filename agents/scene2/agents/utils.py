import os
import re
import shutil
import pandas as pd

# 显式从 scene1.data 导入 (保持你的原始引用)
from scene1.data import (
    MOSFET, MOSFETNet, MOSFETParam, MOSFETCalc,
    VoltageNet, VoltageSource, mosfet_dict, voltage_source_dict,
    sl_mosfet_dict, sl_voltage_source_dict,
)

_SCENE2_AGENTS = os.path.dirname(os.path.abspath(__file__))
_SCENE2 = os.path.dirname(_SCENE2_AGENTS)
_PROMPT_DIR = os.path.join(_SCENE2, "prompt")
_LOOKUP_DIR = os.path.join(_SCENE2, "lookup_tables")

W_MAX_UM = 1000.0
W_MIN_UM = 0.0005   # 全局最小宽度：四位截断下的 0.0005um，防止被截成 0
W_FINGER_THRESHOLD_UM = 50.0
W_FINGER_SCALE = 10

# --- 1. 读取与解析 ---
def read_sys_prompt(name):
    filename = name if name.endswith(".txt") else f"{name}.txt"
    path = os.path.join(_PROMPT_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return {"role": "system", "content": f.read()}

def read_human_prompt(name, **kwargs):
    filename = name if name.endswith(".txt") else f"{name}.txt"
    path = os.path.join(_PROMPT_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if kwargs:
        try: content = content.format(**kwargs)
        except Exception as e: print(f"Warning format prompt: {e}")
    return {"role": "user", "content": content}

def parse_netlist(mosfet_dict, voltage_source_dict, raw_netlist: str):
    lines = raw_netlist.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith((".", "*")): continue
        parts = line.split()
        if len(parts) >= 6:
            name, d, g, s, b, type_ = parts[:6]
            param_matches = re.findall(r"(\w+)\s*=\s*['\"]?([\w\d\.]+)['\"]?", line)
            params = {k.upper(): v for k, v in param_matches}
            def to_f(v, dft):
                try: return float(v)
                except: return dft
            mosfet_dict[name] = MOSFET(name, type_, MOSFETNet(d, g, s, b), 
                                      MOSFETParam(L=to_f(params.get("L"), 0.15), W=to_f(params.get("W"), 1.0), m=to_f(params.get("M"), 1.0)), 
                                      MOSFETCalc())
        elif len(parts) == 4:
            name, pos, neg, val = parts
            dc_m = re.search(r"dc\s*=\s*['\"]?([\d\.]+)['\"]?", val, re.I)
            dc = float(dc_m.group(1)) if dc_m else 0.9
            voltage_source_dict[name] = VoltageSource(name, VoltageNet(pos, neg), dc)

# --- 2. 核心物理同步逻辑 (锁定键名映射) ---
def apply_sizing_to_mosfet_dict(mosfet_dict, sizing: dict):
    """将 SIZING 中的 L / gm_id 写入 mosfet_dict，容忍大小写差异。

    - n 可以是 'xm8' 或 'XM8'，这里会自动在 mosfet_dict 中匹配实际存在的键。
    """
    for n, attrs in (sizing or {}).items():
        key = None
        if n in mosfet_dict:
            key = n
        elif n.upper() in mosfet_dict:
            key = n.upper()
        elif n.lower() in mosfet_dict:
            key = n.lower()
        if key is None:
            continue
        m = mosfet_dict[key]
        if "L" in attrs:
            m.update_param("L", float(attrs["L"]))
        g_val = attrs.get("gm_id", attrs.get("gmid"))
        if g_val is not None:
            m.update_param("gmid", float(g_val))

def update_ids_result(mosfet_dict, Itail: float, tail_device: str = None, topology_config: dict = None):
    """
    根据拓扑分配各管 Ids。
    - 套筒：尾电流管=Itail，其余=Itail/2，不依赖 Agent1 的 scale1。
    - NMCNR：按 current_groups / scale2 / scale3 做分级电流分配。
    """
    cfg = topology_config or {}
    bias_type = cfg.get("bias_type") or ""
    roles = cfg.get("device_roles", {})
    tail_device = tail_device or (cfg.get("tail_device") if isinstance(cfg.get("tail_device"), str) else None)

    # --- 1) 套筒：保持一比二 ---
    if bias_type != "nmcnr":
        for name, m in mosfet_dict.items():
            role = str(roles.get(name, "")).lower()
            is_tail = (name == tail_device) or ("尾电流" in role)
            val = float(Itail) if is_tail else (float(Itail) / 2.0)
            m.update_param("ids", val)
        return

    # --- 2) NMCNR：优先用仿真实测电流（满足 KCL），无则用论文/黄金比例 ---
    actual_ua = cfg.get("nmcnr_actual_ids_ua")
    if actual_ua and isinstance(actual_ua, dict):
        # 按节点/仿真实测分配，保证 KCL（仿真结果必然满足 KCL）
        actual_lower = {str(k).lower(): float(v) for k, v in actual_ua.items()}
        for name, m in mosfet_dict.items():
            key = name.lower() if isinstance(name, str) else str(name).lower()
            val = actual_lower.get(key)
            if val is not None:
                m.update_param("ids", float(val))
            # 若表中无该管则保留原值或跳过（通常 24 管都有）
        return

    groups = cfg.get("current_groups") or {}
    stage1_tail = set(groups.get("stage1_tail", []))
    stage1_main = set(groups.get("stage1_main", []))
    stage2_main = set(groups.get("stage2_main", []))
    stage3_main = set(groups.get("stage3_main", []))
    bias_set = set(groups.get("bias", []))

    I_ref = float(cfg.get("I_ref", 5.0))
    I1_tail = 4.0 * I_ref
    I1_half = 2.0 * I_ref
    I2_per = 1.0 * I_ref
    I3_per = 10.0 * I_ref

    for name, m in mosfet_dict.items():
        if name in stage1_tail:
            val = I1_tail
        elif name in stage1_main:
            val = I1_half
        elif name in stage2_main:
            val = I2_per
        elif name in stage3_main:
            val = I3_per
        elif name in bias_set:
            val = I_ref
        else:
            val = I1_half
        m.update_param("ids", float(val))

def _pick_fixed_ids_table(folder, is_nmos, ids_ua):
    """若 folder/fixed_ids/ 下存在按电流命名的表（如 gmid_nmos_50uA.csv、gmid_pmos_lvt_5uA.csv），返回最接近 ids_ua 的文件名，否则返回 None。"""
    sub = os.path.join(folder, "fixed_ids")
    if not os.path.isdir(sub):
        return None
    want = "nmos" if is_nmos else "pmos"
    pattern = re.compile(r"gmid_(nmos|pmos(?:_lvt)?)_(\d+(?:\.\d+)?)uA\.csv", re.I)
    best_file, best_diff = None, float("inf")
    for f in os.listdir(sub):
        if not f.endswith(".csv"):
            continue
        m = pattern.match(f)
        if not m or want not in m.group(1).lower():
            continue
        try:
            file_ids = float(m.group(2))
        except ValueError:
            continue
        diff = abs(file_ids - ids_ua)
        if diff < best_diff:
            best_diff, best_file = diff, f
    return os.path.join("fixed_ids", best_file) if best_file else None


def lookup_gmid_for_mosfet_dict(mosfet_dict, lookup_dir=None, use_fixed_ids_tables=True):
    """查表 (L, gm_id) -> Vgs, idW。若 use_fixed_ids_tables 且 lookup_dir/fixed_ids/ 下有 gmid_*_*uA.csv，
    则按设备 ids 选最接近电流的表，否则用原表 gmid_nmos.csv / gmid_pmos_lvt.csv。"""
    folder = lookup_dir or _LOOKUP_DIR
    res = {}
    for n, m in mosfet_dict.items():
        lt = float(m.get_param("L"))
        gt = float(m.get_param("gmid") or 15.0)
        ids_ua = float(m.get_param("ids") or 0.0)
        ids_A = ids_ua * 1e-6 if ids_ua > 0 else 1e-9
        is_nmos = "nmos" in m.type.lower() or "nfet" in m.type.lower()
        csv_rel = "gmid_nmos.csv" if is_nmos else "gmid_pmos_lvt.csv"
        if use_fixed_ids_tables and ids_ua > 0:
            fixed = _pick_fixed_ids_table(folder, is_nmos, ids_ua)
            if fixed and os.path.isfile(os.path.join(folder, fixed)):
                csv_rel = fixed
        df = pd.read_csv(os.path.join(folder, csv_rel))
        sub = df[df["L1"].between(lt - 0.05, lt + 0.05)].copy()
        if sub.empty:
            sub = df.iloc[(df["L1"] - lt).abs().argsort()].head(10).copy()
        gmid_col = "gm/id" if "gm/id" in sub.columns else next((c for c in sub.columns if "gm" in c.lower() and "id" in c.lower()), sub.columns[6])
        dist_gmid = (sub[gmid_col] - gt).abs()
        # 先按 gm_id 最近；并列时选表中 Id 最接近设计 ids 的行，再按 index 最小
        best_gmid = dist_gmid[dist_gmid == dist_gmid.min()]
        id_col = "Id" if "Id" in sub.columns else next((c for c in sub.columns if "id" in c.lower() and "w" not in c.lower()), None)
        if id_col and id_col in sub.columns:
            sub_best = sub.loc[best_gmid.index]
            id_vals = sub_best[id_col].fillna(0).astype(float).replace(0, 1e-18)
            idx = (id_vals - ids_A).abs().idxmin()
        else:
            idx = best_gmid.index.min()
        res[n] = sub.loc[idx].to_dict()
    return res

def apply_lookup_to_mosfet_dict(mosfet_dict, lookup_result):
    for n, row in lookup_result.items():
        if n in mosfet_dict:
            m = mosfet_dict[n]
            m.update_param("Vgs", float(row["Vgs"]))
            idw_key = next((k for k in row.keys() if "id" in k.lower() and "w" in k.lower()), None)
            if idw_key: m.update_param("idW", float(row[idw_key]))

# 查表 Id/W 单位：NMOS 表多为 A/µm（量级 1e-5），PMOS 表多为 µA/µm（量级 0.1~10）。用此阈值区分。
_IDW_A_PER_UM_THRESHOLD = 0.01

def update_W_result(mosfet_dict, W_max_um=None):
    """W = Ids/(Id/W)：总宽(µm)。ids 恒为 µA。

    - 若 idW <= 0.01：视为 A/µm（NMOS 表），W_µm = (ids*1e-6)/idW。
    - 若 idW > 0.01：视为 µA/µm（PMOS 表），W_µm = ids/idW。
    写 .cir 时再除以 m 得到每指宽并输出 m=。
    """
    cap = float(W_max_um) if W_max_um is not None else W_MAX_UM
    for dev in mosfet_dict.values():
        ids, idw = dev.get_param("ids"), dev.get_param("idW")
        if idw <= 0:
            continue
        try:
            ids_f = float(ids)
            idw_f = float(idw)
            if idw_f <= _IDW_A_PER_UM_THRESHOLD:
                w_total = (ids_f * 1e-6) / idw_f
            else:
                w_total = ids_f / idw_f
        except Exception:
            continue
        w_clamped = max(W_MIN_UM, min(w_total, cap))
        dev.update_param("W", round(w_clamped, 4))

def snapshot_params(mosfet_dict):
    return {n: {"L": m.get_param("L"), "gm_id": m.get_param("gmid"), "W": m.get_param("W"), 
                "ids": m.get_param("ids"), "idW": m.get_param("idW")} for n, m in mosfet_dict.items()}

# --- 3. 评估与评价 (Agent3 依赖的关键函数) ---
def is_specs_met(gap):
    """判断所有指标是否达标 (Gap >= 0, IDC <= 0)"""
    return all(v >= 0 for k, v in gap.items() if k != "IDC") and gap.get("IDC", 0) <= 0

def compute_fom(results, VDD=1.8):
    """计算 FOM 分数"""
    p = VDD * results.get("i(vmeas)", 0.0)
    if p <= 0: return 0.0
    # FOM = (GBW * Gain_linear) / Power
    return round((results.get("GBW", 0.0) * 10**(results.get("Gain", 0.0)/20.0)) / p, 6)

def compute_gap(results, specs):
    """计算指标差距"""
    g = {}
    for k in ["Gain", "GBW", "PM", "SR"]:
        sk = f"{k}_min"
        if sk in specs: g[k] = round(results.get(k, 0.0) - float(specs[sk]), 2)
    if "IDC_max" in specs: g["IDC"] = round(results.get("i(vmeas)", 0.0) - float(specs["IDC_max"]), 2)
    return g

# --- 4. 仿真执行 (保持原逻辑) ---
def generate_netlist_ota5(mosfet_dict, voltage_source_dict, sl_mosfet_dict=None, sl_voltage_source_dict=None):
    out = [".subckt AMP Vinp Vinn VDD VSS Vout\n"]
    def _w(d):
        for n, m in d.items():
            mod = "sky130_fd_pr__nfet_01v8" if "nmos" in m.type.lower() else "sky130_fd_pr__pfet_01v8_lvt"
            w, l = m.get_param("W"), m.get_param("L")
            if w > W_FINGER_THRESHOLD_UM: out.append(f"{n} {m.net.d} {m.net.g} {m.net.s} {m.net.b} {mod} L={l} W={round(w/W_FINGER_SCALE,2)} m={W_FINGER_SCALE}\n")
            else: out.append(f"{n} {m.net.d} {m.net.g} {m.net.s} {m.net.b} {mod} L={l} W={w}\n")
    _w(mosfet_dict)
    if sl_mosfet_dict: _w(sl_mosfet_dict)
    for vs in voltage_source_dict.values(): out.append(f"{vs.name} {vs.net.pos} {vs.net.neg} dc={vs.dc}\n")
    if sl_voltage_source_dict:
        for vs in sl_voltage_source_dict.values(): out.append(f"{vs.name} {vs.net.pos} {vs.net.neg} dc={vs.dc}\n")
    out.append(".ends AMP\n")
    return "".join(out)

def generate_netlist_nmcnr(mosfet_dict, voltage_source_dict=None, topology_config=None):
    """Leung NMCNR 三级运放子电路，引脚 gnda vdda vinn vinp vout；仅含 xm0–xm23 + I0/C0/C1/R0。

    I0 默认写成 3uA，主流程会替换为 I_ref。C0/C1/R0 若在 topology_config 中给出 nmcnr_C0_pF、nmcnr_C1_pF、nmcnr_R0_kOhm 则使用，否则用 0.4p/0.2p/15k（与可工作点一致）。
    """
    cfg = topology_config or {}
    C0 = cfg.get("nmcnr_C0_pF")
    C1 = cfg.get("nmcnr_C1_pF")
    R0 = cfg.get("nmcnr_R0_kOhm")
    C0_p = f"{float(C0)}p" if C0 is not None else "0.4p"
    C1_p = f"{float(C1)}p" if C1 is not None else "0.2p"
    R0_k = f"{float(R0)}k" if R0 is not None else "15k"

    # 子电路端口为小写 gnda vdda vinn vinp vout，模板里为大写 VDDA/GNDA，需统一为端口名否则电源未接入
    _port_map = {"VDDA": "vdda", "GNDA": "gnda", "VINN": "vinn", "VINP": "vinp", "VOUT": "vout"}
    def _node(net):
        return _port_map.get(net, net)
    out = [".subckt Leung_NMCNR_Pin_3 gnda vdda vinn vinp vout\n", ".PARAM CURRENT_0_BIAS=3u\n"]
    for n in sorted(mosfet_dict.keys(), key=lambda x: (int(x.replace("xm", "")) if x.replace("xm", "").isdigit() else 999)):
        m = mosfet_dict[n]
        mod = "sky130_fd_pr__nfet_01v8" if ("nmos" in m.type.lower() or "nfet" in m.type.lower()) else "sky130_fd_pr__pfet_01v8_lvt"
        w, l = m.get_param("W"), m.get_param("L")
        d, g, s, b = _node(m.net.d), _node(m.net.g), _node(m.net.s), _node(m.net.b)
        try:
            mult = float(m.get_param("m"))
        except (TypeError, ValueError, AttributeError):
            mult = 1
        if mult is None or mult < 1:
            mult = 1
        mult = int(mult)
        # 内部 W 为总宽；.cir 写每指宽 w=W/m 并写 m=，放缩倍数必须进 .cir。
        w_per_finger = w / mult if mult else w
        if w_per_finger > W_FINGER_THRESHOLD_UM:
            w_out = round(w_per_finger / W_FINGER_SCALE, 2)
            m_out = W_FINGER_SCALE * mult
            out.append(f"{n} {d} {g} {s} {b} {mod} l={l} w={w_out} m={m_out}\n")
        else:
            out.append(f"{n} {d} {g} {s} {b} {mod} l={l} w={round(w_per_finger, 4)} m={mult}\n")
    out.append("I0 net013 gnda 'CURRENT_0_BIAS'\n")
    out.append(f"C1 net044 net049 {C1_p}\n")
    out.append(f"C0 net050 net044 {C0_p}\n")
    out.append(f"R0 net044 vout {R0_k}\n")
    out.append(".ends Leung_NMCNR_Pin_3\n")
    return "".join(out)


def apply_nmcnr_unit_width(mosfet_dict, topology_config, I_ref, W_cap=None):
    """NMCNR：在 update_W_result 已按 W=Ids/idW 算完各管 W 的前提下，仅做 parameter_sharing 同步。

    这样「查表得到的 idW + 电流分配 ids」算出的 W 与最终网表/打印的 W 一致，不再用固定 i_unit 覆盖。
    """
    cfg = topology_config or {}
    if cfg.get("bias_type") != "nmcnr":
        return

    # 不再用固定 i_unit 表覆盖 Master 的 W；保持 update_W_result 的 W = ids/idW。
    # 仅将 Master 的 W/L 广播给 Slave，保持参数完全共享
    sharing = cfg.get("parameter_sharing") or {}
    for slave, master in sharing.items():
        ms = mosfet_dict.get(slave) or mosfet_dict.get(slave.upper())
        mm = mosfet_dict.get(master) or mosfet_dict.get(master.upper())
        if not ms or not mm:
            continue
        ms.update_param("W", mm.get_param("W"))
        # L 也保持与 Master 一致（Agent2 已在 L/gm_id 层面对 Master 做控制）
        ms.update_param("L", mm.get_param("L"))

    # main 路径下解析网表时 m='PARAM' 会变成 1；用 topology 的 mult 作为默认 m，保证网表输出正确 m
    golden = cfg.get("nmcnr_golden_LW_um") or {}
    for name, lw in golden.items():
        if not isinstance(lw, dict) or "mult" not in lw:
            continue
        key = name if name in mosfet_dict else (name.upper() if name.upper() in mosfet_dict else None)
        if key:
            try:
                mult_val = int(lw["mult"])
                if mult_val >= 1:
                    mosfet_dict[key].update_param("m", float(mult_val))
            except (TypeError, ValueError, KeyError):
                pass

def run_simulation(netlist: str, cir_name: str, sim_files, sim_dir: str):
    sim_dir = os.path.abspath(sim_dir); os.makedirs(sim_dir, exist_ok=True)
    for old in ["AC_results_1.txt", "Idc_1.txt", "SR_tran.txt", "Op_1.txt"]:
        p = os.path.join(sim_dir, old)
        if os.path.exists(p): os.remove(p)
    # 将当前 netlist 写入仿真目录
    with open(os.path.join(sim_dir, f"{cir_name}.cir"), "w") as f:
        f.write(netlist)
    # 对 NMCNR：同时覆盖 scene2/sim_NMCNR/NMCNR.cir，使 PDK 的 IDC/AC bench (.include 'scene2/sim_NMCNR/NMCNR.cir') 使用最新尺寸
    if cir_name.upper() == "NMCNR":
        try:
            nmcnr_path = os.path.join(_SCENE2, "sim_NMCNR", "NMCNR.cir")
            os.makedirs(os.path.dirname(nmcnr_path), exist_ok=True)
            with open(nmcnr_path, "w") as f_nm:
                f_nm.write(netlist)
        except Exception:
            pass
    project_root = os.path.abspath(os.path.join(sim_dir, "..", ".."))
    # scene2/models/sky130.lib.spice 内 .include "../cells/..." 相对 scene2/models 解析 → 需要 scene2/cells 存在
    scene2_dir = os.path.dirname(sim_dir)
    cells_link = os.path.join(scene2_dir, "cells")
    cells_target = os.path.join(project_root, "cells")
    if os.path.isdir(cells_target) and not os.path.exists(cells_link):
        try:
            os.symlink(cells_target, cells_link)
        except OSError:
            pass
    cwd = os.getcwd()
    # 与 folded_AC 一致：从项目根运行，.cir 内用相对项目根路径，使 .lib 与 alias 同作用域
    try:
        for f in (sim_files or []):
            fpath = os.path.join(sim_dir, f)
            run_path = fpath
            # NMCNR：完全参考 NMCMR1.cir 成功经验——.lib 绝对路径 + .PARAM + 参数化子电路（l='X' w='Y'）
            if cir_name.upper() == "NMCNR" and os.path.isfile(fpath):
                nmcmr1_path = os.path.join(_SCENE2, "sim_NMCNR", "NMCMR1.cir")
                if os.path.isfile(nmcmr1_path):
                    with open(nmcmr1_path, "r", encoding="utf-8") as fp:
                        nmcmr1 = fp.read()
                    # 从 netlist 解析各管 L/W/m，映射到 NMCMR1 的 .PARAM 名
                    dev_vals = {}
                    for line in netlist.split("\n"):
                        m = re.search(r"(xm\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+l=([\d.]+)\s+w=([\d.]+)\s+m=(\d+)", line)
                        if m:
                            dev_vals[m.group(1).lower()] = {"L": float(m.group(2)), "W": float(m.group(3)), "m": int(m.group(4))}
                    # NMCMR1 参数映射：主管 -> 参数名
                    param_map = [
                        ("xm0", "MOSFET_0_8_L_BIASCM_PMOS", "MOSFET_0_8_W_BIASCM_PMOS", "MOSFET_0_8_M_BIASCM_PMOS"),
                        ("xm8", "MOSFET_8_2_L_gm1_PMOS", "MOSFET_8_2_W_gm1_PMOS", "MOSFET_8_2_M_gm1_PMOS"),
                        ("xm10", "MOSFET_10_1_L_gm2_PMOS", "MOSFET_10_1_W_gm2_PMOS", "MOSFET_10_1_M_gm2_PMOS"),
                        ("xm23", "MOSFET_23_1_L_gm3_NMOS", "MOSFET_23_1_W_gm3_NMOS", "MOSFET_23_1_M_gm3_NMOS"),
                        ("xm12", "MOSFET_17_7_L_BIASCM_NMOS", "MOSFET_17_7_W_BIASCM_NMOS", "MOSFET_17_7_M_BIASCM_NMOS"),
                        ("xm21", "MOSFET_21_2_L_LOAD2_NMOS", "MOSFET_21_2_W_LOAD2_NMOS", "MOSFET_21_2_M_LOAD2_NMOS"),
                    ]
                    param_lines = [".PARAM", "+ CURRENT_0_BIAS=5.0u", "+ VDD_VAL=1.8", "+ VCM_VAL=0.9", "+ CLOAD=10p"]
                    for dev, pl, pw, pm in param_map:
                        v = dev_vals.get(dev, {})
                        if v:
                            param_lines.append(f"+ {pl}={v['L']}")
                            param_lines.append(f"+ {pw}={v['W']}")
                            param_lines.append(f"+ {pm}={v['m']}")
                    param_lines.extend(["+ RESISTOR_0=15k", "+ CAPACITOR_0=0.4p", "+ CAPACITOR_1=0.2p"])
                    param_block = "\n".join(param_lines)
                    # 替换 NMCMR1 的 .PARAM 块（从 .PARAM 到 RESISTOR_0 行）
                    content = re.sub(
                        r"\.PARAM\s*\n(?:\+[^\n]+\n)+",
                        param_block + "\n",
                        nmcmr1,
                        count=1,
                    )
                    # 替换 .control 为 IDC/AC bench 所需
                    if "IDC" in f:
                        ctrl = "\n.op\n.CONTROL\nrun\nop\nprint i(vmeas) > Idc_1.txt\n"
                        for i in range(24):
                            mod = "msky130_fd_pr__pfet_01v8_lvt" if i < 12 else "sky130_fd_pr__nfet_01v8"
                            op_redir = " > Op_1.txt" if i == 0 else " >> Op_1.txt"
                            ctrl += f"print @m.xx1.xm{i}.m{mod}[vds] @m.xx1.xm{i}.m{mod}[vgs] @m.xx1.xm{i}.m{mod}[vth]{op_redir}\n"
                        ctrl += ".ENDC\n.END\n"
                    else:
                        ctrl = "\n.ac dec 100 1 1000Meg\n.print ac vdb(vout) vp(vout)\n.CONTROL\nset units=degrees\nrun\nmeas ac gain_max find vdb(vout) at=1\nmeas ac gbw when vdb(vout)=0\nmeas ac phase_margin find vp(vout) when vdb(vout)=0\necho \"Gain= $&gain_max\" > AC_results_1.txt\necho \"GBW= $&gbw\" >> AC_results_1.txt\necho \"PM= $&phase_margin\" >> AC_results_1.txt\n.ENDC\n.END\n"
                    content = re.sub(r"\.control[\s\S]*?\.end\s*$", ctrl, content, flags=re.IGNORECASE)
                    # VVDD -> VDD_SRC + VMEAS（仅 IDC 需测电流）
                    if "IDC" in f:
                        content = content.replace("VVDD vdd 0 'VDD_VAL'", "VDD_SRC vdd_src 0 dc=1.8\nVMEAS vdd_src vdd 0")
                    run_path = os.path.join(sim_dir, "_run_" + f)
                    with open(run_path, "w", encoding="utf-8") as fp:
                        fp.write(content)
                else:
                    # fallback：原逻辑
                    with open(fpath, "r", encoding="utf-8") as fp:
                        content = fp.read()
                    lib_abs = os.path.abspath(os.path.join(project_root, "scene2", "models", "sky130.lib.spice"))
                    content = content.replace(".lib 'scene2/models/sky130.lib.spice'", ".lib '" + lib_abs + "'")
                    content = content.replace(".include 'scene2/sim_NMCNR/NMCNR.cir'", netlist.strip())
                    run_path = os.path.join(sim_dir, "_run_" + f)
                    with open(run_path, "w", encoding="utf-8") as fp:
                        fp.write(content)
            os.chdir(project_root)
            rel = os.path.relpath(run_path, project_root)
            os.system(f"ngspice -b {rel} > {sim_dir}/log 2>&1")
        # ngspice 在 project_root 下运行时，echo > file 会写到根目录，需拉回 sim_dir 供 read_simulation_results 读取
        for fname in ["AC_results_1.txt", "Idc_1.txt", "Op_1.txt", "SR_tran.txt"]:
            src = os.path.join(project_root, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(sim_dir, fname))
                try: os.remove(src)
                except Exception: pass
    finally: os.chdir(cwd)

def read_operating_point(sim_dir, device_names, mosfet_dict):
    path = os.path.join(sim_dir, "Op_1.txt")
    if not os.path.exists(path): return {}
    with open(path, "r") as f: data = [l.strip() for l in f if l.strip()]
    vals = []
    for l in data:
        if "=" in l: vals.append(float(l.split("=")[1]))
        else: vals.extend([float(x) for x in l.split()])
    out = {}
    for i, name in enumerate(device_names):
        if i*3+2 < len(vals):
            vds, vgs, vth = vals[i*3], vals[i*3+1], vals[i*3+2]
            is_p = "p" in str(mosfet_dict[name].type).lower() if name in mosfet_dict else False
            # PMOS: ngspice 常以正 Vds 报告，饱和条件 |Vds|>=|Vgs-Vth| 统一为 vds >= vgs-vth-0.05
            sat = (vds >= (vgs - vth - 0.05)) if is_p else (vds >= (vgs - vth - 0.05))
            out[name] = {"Vds": vds, "Vgs": vgs, "Vth": vth, "saturation": sat}
    return out

def read_simulation_results(sim_dir):
    """读 AC_results_1.txt / Idc_1.txt / SR_tran.txt。若 AC meas 未成立(如增益未过 0dB)，
    文件可能为空或缺少 Gain/GBW/PM 行，未解析到的键保持 0.0。"""
    # 文件中 GBW 为 Hz，读入后除以 1e6 存为 MHz；i(vmeas) 为 A，乘 1e6 存为 µA
    res = {"Gain": 0.0, "GBW": 0.0, "PM": 0.0, "i(vmeas)": 0.0, "SR": 0.0}
    for fn in ["AC_results_1.txt", "Idc_1.txt"]:
        p = os.path.join(sim_dir, fn)
        if os.path.exists(p):
            with open(p, "r") as f:
                for ln in f:
                    if "=" in ln:
                        k, v = [x.strip() for x in ln.split("=", 1)]
                        if not v:
                            continue
                        try:
                            val = float(v)
                        except ValueError:
                            continue
                        if k == "GBW":
                            res["GBW"] = val / 1e6
                        elif "i(vmeas)" in k.lower():
                            res["i(vmeas)"] = val * 1e6
                        elif k in res:
                            res[k] = val
    # NMCNR AC bench 当前写入的 "PM" 多数为 unity-gain 处的输出相位 vp(vout)，不是相位裕度。
    # 将相位换算为相位裕度：PM = 180 + phase_unity，并折叠到 [0, 180]。
    if res.get("PM", 0.0) < 0.0 or res.get("PM", 0.0) > 180.0:
        phase = float(res.get("PM", 0.0))
        pm = (180.0 + phase + 360.0) % 360.0
        if pm > 180.0:
            pm = 360.0 - pm
        res["PM"] = pm
    # SR: 从 SR_tran.txt 取 time / v(vout) 计算 max dV/dt (V/s)，再转为 V/us
    # ngspice wrdata 可能输出多列（如 time 重复），时间为第 1 列、v(vout) 为最后一列
    sr_path = os.path.join(sim_dir, "SR_tran.txt")
    if os.path.exists(sr_path):
        try:
            t_list, v_list = [], []
            with open(sr_path, "r") as f:
                for ln in f:
                    parts = ln.split()
                    if len(parts) >= 2:
                        try:
                            t_list.append(float(parts[0]))
                            v_list.append(float(parts[-1]))
                        except ValueError:
                            continue
            if len(t_list) >= 2 and len(v_list) >= 2:
                slope_max = 0.0
                for i in range(len(t_list) - 1):
                    dt = t_list[i + 1] - t_list[i]
                    if dt > 0:
                        slope = abs(v_list[i + 1] - v_list[i]) / dt
                        if slope > slope_max:
                            slope_max = slope
                res["SR"] = round(slope_max / 1e6, 2)  # V/s -> V/us
        except Exception:
            pass
    return res

def compute_gap(results, specs):
    g = {}
    for k in ["Gain", "GBW", "PM", "SR"]:
        sk = f"{k}_min"
        if sk in specs and specs[sk] is not None:
            try:
                target = float(specs[sk])
            except (TypeError, ValueError):
                continue
            g[k] = round(results.get(k, 0.0) - target, 2)
    idc_max = specs.get("IDC_max", None)
    if idc_max is not None:
        try:
            idc_target = float(idc_max)
            g["IDC"] = round(results.get("i(vmeas)", 0.0) - idc_target, 2)
        except (TypeError, ValueError):
            pass
    return g

def run_sizing_and_get_op(mosfet_dict, voltage_source_dict, sizing, itail, lookup_dir, sim_dir, sim_files, VDD, VSS, VCM, topology_config=None):
    apply_sizing_to_mosfet_dict(mosfet_dict, sizing)
    update_ids_result(mosfet_dict, itail, topology_config=topology_config)
    apply_lookup_to_mosfet_dict(mosfet_dict, lookup_gmid_for_mosfet_dict(mosfet_dict, lookup_dir))
    update_vb_result(mosfet_dict, voltage_source_dict, VDD, VSS, VCM, bias_type=(topology_config or {}).get("bias_type"), topology_config=topology_config)
    cfg = topology_config or {}
    # NMCNR 不再单独封顶 W，与 gm/id 方法一致，仅用全局 W_MAX_UM
    W_cap = float(cfg.get("nmcnr_W_max_um")) if cfg.get("bias_type") == "nmcnr" and cfg.get("nmcnr_W_max_um") is not None else None
    update_W_result(mosfet_dict, W_max_um=W_cap)
    # NMCNR：在 W=Ids/idW 初算后，通过 parameter_sharing 同步 Master → Slave。
    if cfg.get("bias_type") == "nmcnr":
        apply_nmcnr_unit_width(mosfet_dict, cfg, cfg.get("I_ref"), W_cap)
        # 为了让 Agent2 在 NMCNR 下也能从稳定工作点起步，这里同 main.py 一样，
        # 在生成网表前用 nmcnr_golden_LW_um 强制覆盖一次 L/W/m。
        golden = cfg.get("nmcnr_golden_LW_um") or {}
        for name, lw in golden.items():
            if not isinstance(lw, dict):
                continue
            key = name if name in mosfet_dict else (name.upper() if name.upper() in mosfet_dict else None)
            if not key:
                continue
            if "L" in lw:
                mosfet_dict[key].update_param("L", float(lw["L"]))
            mult_val = int(lw["mult"]) if lw.get("mult") is not None else 1
            if "mult" in lw:
                try:
                    mosfet_dict[key].update_param("m", float(mult_val))
                except Exception:
                    pass
            if "W" in lw:
                w_tot = float(lw["W"]) * max(1, mult_val)
                mosfet_dict[key].update_param("W", round(w_tot, 4))
        netlist = generate_netlist_nmcnr(mosfet_dict, topology_config=cfg)
        # 将 I0=3u 替换为 Agent 设定的 I_ref，实现整体缩放但保持比例不变
        I_ref_val = cfg.get("I_ref")
        if I_ref_val is not None:
            try:
                netlist = netlist.replace("CURRENT_0_BIAS=3u", f"CURRENT_0_BIAS={float(I_ref_val)}u")
            except Exception:
                pass
        cir_name = "NMCNR"
    else:
        netlist = generate_netlist_ota5(mosfet_dict, voltage_source_dict)
        cir_name = "AMP"
    run_simulation(netlist, cir_name, sim_files, sim_dir)
    device_names = list(mosfet_dict.keys())
    if cfg.get("bias_type") == "nmcnr":
        device_names = sorted(device_names, key=lambda x: int(x[2:]) if len(x) > 2 and x[2:].isdigit() else 999)
    return {"device_op": read_operating_point(sim_dir, device_names, mosfet_dict), "results": read_simulation_results(sim_dir)}

def update_vb_result(mosfet_dict, voltage_source_dict, VDD, VSS, VCM, bias_type=None, lookup_dir=None, topology_config=None):
    cfg = topology_config or {}

    # NMCNR 三级运放：偏置由 I0 与内部网络自行设定，此处不再套用套筒公式，避免张冠李戴
    if (bias_type or cfg.get("bias_type")) == "nmcnr":
        return

    vgs = lambda n: float(mosfet_dict[n].get_param("Vgs") or 0.7) if n in mosfet_dict else 0.7
    offsets = cfg.get("bias_offset") or {}
    if "VBN1" in voltage_source_dict:
        voltage_source_dict["VBN1"].update_dc(round(vgs("XM9") + float(offsets.get("VBN1", 0)), 2))
    if "VBN2" in voltage_source_dict:
        base = VCM - vgs("XM1") + (VDD + vgs("XM7") - VCM + vgs("XM1")) / 2 + vgs("XM3")
        voltage_source_dict["VBN2"].update_dc(round(base + float(offsets.get("VBN2", 0)), 2))
    if "VBP" in voltage_source_dict:
        voltage_source_dict["VBP"].update_dc(round(VDD + vgs("XM7") / 2 + vgs("XM5") + float(offsets.get("VBP", 0)), 2))

def parse_netlist_sl(sl_mosfet_dict, sl_voltage_source_dict, raw_netlist: str):
    parse_netlist(sl_mosfet_dict, sl_voltage_source_dict, raw_netlist)

def read_operating_point(sim_dir, device_names, mosfet_dict):
    path = os.path.join(sim_dir, "Op_1.txt")
    if not os.path.exists(path): return {}
    with open(path, "r") as f: data = [l.strip() for l in f if l.strip()]
    vals = []
    for l in data:
        if "=" in l: vals.append(float(l.split("=")[1]))
        else: vals.extend([float(x) for x in l.split()])
    out = {}
    for i, name in enumerate(device_names):
        if i*3+2 < len(vals):
            vds, vgs, vth = vals[i*3], vals[i*3+1], vals[i*3+2]
            is_p = "p" in str(mosfet_dict[name].type).lower() if name in mosfet_dict else False
            # PMOS: ngspice 常以正 Vds 报告，饱和条件 |Vds|>=|Vgs-Vth| 统一为 vds >= vgs-vth-0.05
            sat = (vds >= (vgs - vth - 0.05)) if is_p else (vds >= (vgs - vth - 0.05))
            out[name] = {"Vds": vds, "Vgs": vgs, "Vth": vth, "saturation": sat}
    return out