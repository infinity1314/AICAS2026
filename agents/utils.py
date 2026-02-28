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
    for n, attrs in (sizing or {}).items():
        if n in mosfet_dict:
            m = mosfet_dict[n]
            if "L" in attrs: m.update_param("L", float(attrs["L"]))
            g_val = attrs.get("gm_id", attrs.get("gmid"))
            if g_val is not None: m.update_param("gmid", float(g_val))

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

    # --- 2) NMCNR：按 stage1/2/3 与 scale2/scale3 分配 ---
    groups = cfg.get("current_groups") or {}
    stage1_tail = set(groups.get("stage1_tail", []))
    stage1_main = set(groups.get("stage1_main", []))
    stage2_main = set(groups.get("stage2_main", []))
    stage3_main = set(groups.get("stage3_main", []))
    bias_set = set(groups.get("bias", []))

    I_ref = float(cfg.get("I_ref", Itail))
    scale2 = float(cfg.get("scale2", 0.0) or 0.0)
    scale3 = float(cfg.get("scale3", 0.0) or 0.0)

    # 物理保底：三级放大器的后级电流不能无限缩小
    s2_min_nmcnr = float(cfg.get("scale2_min_nmcnr", 1.0) or 1.0)
    s3_min_nmcnr = float(cfg.get("scale3_min_nmcnr", 4.0) or 4.0)
    scale2 = max(scale2, s2_min_nmcnr)
    scale3 = max(scale3, s3_min_nmcnr)
    # 第三级比例锁死在 4~8，否则会出现 I3=1.3mA、输出级 43× 第一级的失控（Gain 负、GBW=0）
    scale3 = min(scale3, 8.0)

    I1 = float(Itail)
    I_ref_for_I3 = max(I_ref, 5.0)
    I2 = scale2 * I_ref if scale2 > 0 else I1
    I3_raw = scale3 * I_ref_for_I3 if scale3 > 0 else I2
    I3 = min(I3_raw, I1 * 10.0)  # 第三级电流上限 10×I1，避免输出级 43× 第一级、vout 锁轨

    # 如果显式给出各支路电流比例（相对 I_ref），则优先使用
    ratio_map = {}
    ratios = cfg.get("current_ratios") or {}
    if isinstance(ratios, dict) and ratios:
        name_map = {n.lower(): n for n in mosfet_dict.keys()}
        for k, v in ratios.items():
            if v is None:
                continue
            key = str(k).lower()
            if key in name_map:
                try:
                    ratio_map[name_map[key]] = float(v)
                except (TypeError, ValueError):
                    pass

    for name, m in mosfet_dict.items():
        if ratio_map and name in ratio_map:
            m.update_param("ids", float(ratio_map[name]) * I_ref)
            continue
        if name in stage1_tail:
            val = I1
        elif name in stage1_main:
            val = I1 / 2.0
        elif name in stage2_main:
            val = I2 / 2.0
        elif name in stage3_main:
            val = I3 / 2.0
        elif name in bias_set:
            val = I_ref  # 偏置管给较小电流；I_ref 通常远小于 Itail
        else:
            # 未标注角色的器件，给一个保守值（按第一级非尾处理）
            val = I1 / 2.0
        m.update_param("ids", float(val))

def lookup_gmid_for_mosfet_dict(mosfet_dict, lookup_dir=None):
    folder = lookup_dir or _LOOKUP_DIR
    res = {}
    for n, m in mosfet_dict.items():
        lt, gt = float(m.get_param("L")), float(m.get_param("gmid") or 15.0)
        csv = "gmid_nmos.csv" if ("nmos" in m.type.lower() or "nfet" in m.type.lower()) else "gmid_pmos_lvt.csv"
        df = pd.read_csv(os.path.join(folder, csv))
        sub = df[df["L1"].between(lt - 0.05, lt + 0.05)]
        if sub.empty: sub = df.iloc[(df["L1"] - lt).abs().argsort()].head(10)
        idx = (sub["gm/id"] - gt).abs().idxmin()
        res[n] = sub.loc[idx].to_dict()
    return res

def apply_lookup_to_mosfet_dict(mosfet_dict, lookup_result):
    for n, row in lookup_result.items():
        if n in mosfet_dict:
            m = mosfet_dict[n]
            m.update_param("Vgs", float(row["Vgs"]))
            idw_key = next((k for k in row.keys() if "id" in k.lower() and "w" in k.lower()), None)
            if idw_key: m.update_param("idW", float(row[idw_key]))

def update_W_result(mosfet_dict, W_max_um=None):
    """W = ids/idW（严格 gm/id 方法）；W_max_um 为 None 时用全局 W_MAX_UM，不破坏查表一致性。"""
    cap = float(W_max_um) if W_max_um is not None else W_MAX_UM
    for m in mosfet_dict.values():
        ids, idw = m.get_param("ids"), m.get_param("idW")
        if idw > 0: m.update_param("W", round(min(float(ids/idw), cap), 2))

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

def generate_netlist_nmcnr(mosfet_dict, voltage_source_dict=None):
    """Leung NMCNR 三级运放子电路，引脚 gnda vdda vinn vinp vout；仅含 xm0–xm23 + I0/C0/C1/R0。"""
    out = [".subckt Leung_NMCNR_Pin_3 gnda vdda vinn vinp vout\n"]
    for n in sorted(mosfet_dict.keys(), key=lambda x: (int(x.replace("xm", "")) if x.replace("xm", "").isdigit() else 999)):
        m = mosfet_dict[n]
        mod = "sky130_fd_pr__nfet_01v8" if ("nmos" in m.type.lower() or "nfet" in m.type.lower()) else "sky130_fd_pr__pfet_01v8"
        w, l = m.get_param("W"), m.get_param("L")
        if w > W_FINGER_THRESHOLD_UM:
            out.append(f"{n} {m.net.d} {m.net.g} {m.net.s} {m.net.b} {mod} L={l} W={round(w/W_FINGER_SCALE,2)} m={W_FINGER_SCALE}\n")
        else:
            out.append(f"{n} {m.net.d} {m.net.g} {m.net.s} {m.net.b} {mod} L={l} W={w}\n")
    out.append("I0 net013 gnda dc 10u\n")
    out.append("C1 net044 net049 1p\n")
    out.append("C0 net050 net044 1p\n")
    out.append("R0 net044 vout 1k\n")
    out.append(".ends Leung_NMCNR_Pin_3\n")
    return "".join(out)

def run_simulation(netlist: str, cir_name: str, sim_files, sim_dir: str):
    sim_dir = os.path.abspath(sim_dir); os.makedirs(sim_dir, exist_ok=True)
    for old in ["AC_results_1.txt", "Idc_1.txt", "SR_tran.txt", "Op_1.txt"]:
        p = os.path.join(sim_dir, old)
        if os.path.exists(p): os.remove(p)
    with open(os.path.join(sim_dir, f"{cir_name}.cir"), "w") as f: f.write(netlist)
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
    # 与 folded_AC 一致：从项目根运行，.cir 内用相对项目根路径，使 .lib 与 alias 同作用域，避免 "could not find a valid modelname"
    run_from_sim_dir = False
    try:
        for f in (sim_files or []):
            os.chdir(project_root)
            rel = os.path.relpath(os.path.join(sim_dir, f), project_root)
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
    if cfg.get("bias_type") == "nmcnr":
        netlist = generate_netlist_nmcnr(mosfet_dict)
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
