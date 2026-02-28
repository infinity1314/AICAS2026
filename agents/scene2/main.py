import os
import sys
import json
from datetime import datetime

# --- 1. 路径修复：确保 Python 能够找到项目根目录与本地 state ---
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)

if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 强制导入本地 scene2/state.py
import state as _state

# 基础物理模型
from scene1.data import (
    mosfet_dict, voltage_source_dict, 
    sl_mosfet_dict, sl_voltage_source_dict
)
from scene2.topology_config import get_topology_config

# 导入本地 Agents 和 工具
from scene2.agents.agent0 import CircuitAgent
from scene2.agents.agent1 import SpecAgent
from scene2.agents.agent2 import ConstraintAgent
from scene2.agents.agent3 import SizingAgent
from scene2.agents.calc_params import compute_design_params
from scene2.agents.utils import *

# 常量定义
RESULTS_DIR = os.path.join(_current_dir, "results")
VDD, VSS, VCM = 1.8, 0.0, 0.9

# 日志记录类
class _Tee:
    def __init__(self, stream, file_handle):
        self._stream, self._file = stream, file_handle
    def write(self, data):
        self._stream.write(data)
        if self._file and not self._file.closed:
            self._file.write(data); self._file.flush()
    def flush(self):
        self._stream.flush()

def run(user_input, netlist_prompt: str = "sys0_sleeve.txt", iter_max: int = 15):
    # 重置局部状态
    _state.specs = {}; _state.constraints = {}; _state.history = []
    _state.best_fom = -1.0; _state.best_params = {}; _state.best_results = {}; _state.best_iter = -1
    
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(RESULTS_DIR, f"run_{ts}.txt")
    
    _orig_stdout = sys.stdout
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            sys.stdout = _Tee(_orig_stdout, f)
            print(f"scene2 run started at {datetime.now().isoformat()}")
            return _run_impl(user_input, netlist_prompt, iter_max)
    finally:
        sys.stdout = _orig_stdout

def _run_impl(user_input, netlist_prompt: str, iter_max: int):
    # --- A. 环境与拓扑初始化 ---
    topology_config = get_topology_config(netlist_prompt)
    # 命令行可覆盖补偿网络参数（仅对 NMCNR 生效）：C0_pF, C1_pF, R0_kOhm
    if isinstance(user_input, dict):
        if user_input.get("C0_pF") is not None:
            topology_config["nmcnr_C0_pF"] = float(user_input["C0_pF"])
        if user_input.get("C1_pF") is not None:
            topology_config["nmcnr_C1_pF"] = float(user_input["C1_pF"])
        if user_input.get("R0_kOhm") is not None:
            topology_config["nmcnr_R0_kOhm"] = float(user_input["R0_kOhm"])
    _state.topology_config = topology_config
    _state.SIM_DIR = os.path.join(_current_dir, topology_config.get("sim_subdir", "sim"))
    _state.LOOKUP_DIR = os.path.join(_current_dir, "lookup_tables")

    agent0 = CircuitAgent()
    topology = agent0.invoke(netlist_prompt)
    content = read_sys_prompt(netlist_prompt).get("content", "")
    # Agent0 返回空或全 unknown 时，用 topology_config.fallback_roles 补全，避免 Agent2 按 unknown 乱给约束
    dr = topology.get("device_roles") or {}
    if not dr or all(v == "unknown" for v in dr.values()):
        fallback = topology_config.get("fallback_roles") or {}
        topology["device_roles"] = {d: fallback.get(d) or fallback.get(d.upper()) or "unknown" for d in topology.get("devices") or []}
        if fallback:
            print("  Agent0 使用 topology_config.fallback_roles 补全 device_roles")
    
    mosfet_dict.clear(); voltage_source_dict.clear(); sl_mosfet_dict.clear(); sl_voltage_source_dict.clear()
    parse_netlist(mosfet_dict, voltage_source_dict, content)

    # Sleeve 缝合逻辑
    if "sleeve" in netlist_prompt.lower():
        try:
            content_sl = read_sys_prompt("sys0_1_sl.txt").get("content", "")
            if content_sl.strip(): parse_netlist_sl(sl_mosfet_dict, sl_voltage_source_dict, content_sl)
        except: pass

    # Agent1 指标解析
    agent1 = SpecAgent()
    specs = agent1.invoke(user_input, netlist_content=content)
    if topology.get("device_roles"):
        specs["device_roles"] = dict(topology["device_roles"])
    _state.specs = specs

    # 根据拓扑类型计算设计初值（scale1/2/3, Itail 等）
    topo_key = (netlist_prompt or "").upper()
    topology_type = "nmcnr" if "NMCNR" in topo_key else "folded"
    design_params = compute_design_params(specs, topology_type=topology_type)

    _state.I_ref = float(design_params.get("I_ref", 100.0))
    _state.scale1 = float(design_params.get("scale1", 1.0))
    _state.Itail = float(design_params.get("Itail", _state.scale1 * _state.I_ref))
    _state.scale2_min = design_params.get("scale2_min")
    _state.scale2 = design_params.get("scale2", 0.0)
    _state.scale3 = design_params.get("scale3", 0.0)

    # 三级运放：有 KCL 电流集(nmcnr_actual_ids_ua)时网表 I0=nmcnr_I0_ua、Itail=表内 xm4，否则用 nmcnr_I_ref 与 4*I_ref
    if topology_type == "nmcnr":
        actual = topology_config.get("nmcnr_actual_ids_ua") or {}
        if actual:
            _state.I_ref = float(topology_config.get("nmcnr_I0_ua", 95.0))
            _state.Itail = float(actual.get("xm4", 20.0))
        else:
            _state.I_ref = float(topology_config.get("nmcnr_I_ref", 5.0))
            _state.Itail = 4.0 * _state.I_ref
        _state.topology_config["I_ref"] = _state.I_ref

    # 将关键参数写入 topology_config，供 Agent2 与电流分配使用
    _state.topology_config["device_roles"] = _state.specs.get("device_roles") or {}
    _state.topology_config["scale1"] = _state.scale1
    _state.topology_config["scale2"] = _state.scale2
    _state.topology_config["scale3"] = _state.scale3
    _state.topology_config["I_ref"] = _state.I_ref

    sim_files = topology_config.get("sim_files_pdk" if os.environ.get("SCENE1_USE_PDK", "1")=="1" else "sim_files_compact")

    # 定义仿真闭包供 Agent2 使用
    def simulate_fn(sizing):
        cfg = {**_state.topology_config, "device_names": list(mosfet_dict.keys()), 
               "scale1": _state.scale1, "I_ref": _state.I_ref, "nmcnr_bias": getattr(_state, 'nmcnr_bias', None)}
        return run_sizing_and_get_op(mosfet_dict, voltage_source_dict, sizing, _state.Itail, _state.LOOKUP_DIR, _state.SIM_DIR, sim_files, VDD, VSS, VCM, topology_config=cfg)

    # Agent2 饱和区预调
    agent2 = ConstraintAgent()
    constraints_res = agent2.invoke(specs, topology, simulate_fn=simulate_fn, topology_config=_state.topology_config)
    _state.constraints = {"devices": constraints_res.get("devices", {})}
    
    current_sizing = constraints_res.get("_initial_sizing")
    device_names = list(mosfet_dict.keys())

    # --- B. 闭环调优循环 ---
    for k in range(iter_max):
        print(f"\n--- 迭代 {k + 1}/{iter_max} ---")
        
        if k > 0:
            agent3 = SizingAgent()
            raw = agent3.invoke(_state.constraints["devices"], _state.history, device_names=device_names, 
                                topology=topology, specs=_state.specs, itail=_state.Itail,
                                topology_config=_state.topology_config)
            if "think" in raw: print("  推理原文:\n", raw.pop("think"))
            # 大小写不敏感匹配：Agent3 可能返回 XM8/xm8，device_names 来自 mosfet_dict 需一致
            device_canonical = {str(d).lower(): d for d in device_names}
            current_sizing = {}
            for key, v in raw.items():
                if not isinstance(v, dict):
                    continue
                k = device_canonical.get(str(key).lower())
                if k is not None:
                    current_sizing[k] = v
            cfg = _state.topology_config or {}
            if cfg.get("bias_type") == "nmcnr":
                # 输入对 xm8/xm9 必须对称，否则差分对不对称导致 GBW/Gain 异常
                if "xm8" in current_sizing and "xm9" in current_sizing and isinstance(current_sizing["xm8"], dict) and isinstance(current_sizing["xm9"], dict):
                    L8, g8 = current_sizing["xm8"].get("L"), current_sizing["xm8"].get("gm_id")
                    L9, g9 = current_sizing["xm9"].get("L"), current_sizing["xm9"].get("gm_id")
                    L_sym = max(float(L8 or 0), float(L9 or 0))
                    g_sym = round((float(g8 or 0) + float(g9 or 0)) / 2.0, 2)
                    current_sizing["xm8"] = {"L": round(L_sym, 3), "gm_id": g_sym}
                    current_sizing["xm9"] = {"L": round(L_sym, 3), "gm_id": g_sym}
                # parameter_sharing：Slave 继承 Master 的 L/gm_id
                sharing = cfg.get("parameter_sharing", {})
                if sharing:
                    for slave, master in sharing.items():
                        if slave in current_sizing and master in current_sizing:
                            current_sizing[slave]["L"] = current_sizing[master].get("L", current_sizing[slave]["L"])
                            current_sizing[slave]["gm_id"] = current_sizing[master].get("gm_id", current_sizing[slave]["gm_id"])
                # 第三级输出管 xm23 单轮修改量限制更严，避免 gm_id 小步就触发查表 W 爆炸
                xm23_key = "xm23" if "xm23" in current_sizing else ("XM23" if "XM23" in current_sizing else None)
                if xm23_key and _state.history:
                    last_p = _state.history[-1].get("params") or {}
                    prev_23 = last_p.get("xm23") or last_p.get("XM23") or {}
                    L_prev = float(prev_23.get("L", 2.0))
                    g_prev = float(prev_23.get("gm_id", 3.1))
                    p = current_sizing[xm23_key]
                    L_new = p.get("L"); g_new = p.get("gm_id")
                    if L_new is not None:
                        dL = float(L_new) - L_prev
                        if abs(dL) > 0.10:
                            L_new = round(L_prev + (0.10 if dL > 0 else -0.10), 3)
                            p["L"] = L_new
                    if g_new is not None:
                        dg = float(g_new) - g_prev
                        if abs(dg) > 0.5:
                            g_new = round(g_prev + (0.5 if dg > 0 else -0.5), 2)
                            p["gm_id"] = g_new

        if not current_sizing:
            if k > 0:
                print("  [跳过] Agent3 未返回有效 sizing（或键名与 device_names 不匹配），跳过本轮回仿。")
            continue

        # 【核心修正：物理链条强同步】
        apply_sizing_to_mosfet_dict(mosfet_dict, current_sizing)
        update_ids_result(mosfet_dict, _state.Itail, tail_device=_state.topology_config.get("tail_device"), topology_config=_state.topology_config)
        
        # 查表同步 (gm/ID -> idW)
        lookup_res = lookup_gmid_for_mosfet_dict(mosfet_dict, _state.LOOKUP_DIR)
        apply_lookup_to_mosfet_dict(mosfet_dict, lookup_res) # 确保 idW 被精准写回
        
        # 刷新偏置与最终尺寸 (Ids / idW -> W)
        update_vb_result(mosfet_dict, voltage_source_dict, VDD, VSS, VCM, bias_type=topology_config.get("bias_type"), topology_config=_state.topology_config)
        # NMCNR 不单独封顶 W，保持 gm/id 一致性
        W_cap = float(_state.topology_config.get("nmcnr_W_max_um")) if _state.topology_config.get("bias_type") == "nmcnr" and _state.topology_config.get("nmcnr_W_max_um") is not None else None
        update_W_result(mosfet_dict, W_max_um=W_cap)
        # NMCNR：仅做 parameter_sharing（W/L 从 Master 拷到 Slave），W 保持上面 update_W_result 的 ids/idW，与查表一致
        if _state.topology_config.get("bias_type") == "nmcnr":
            apply_nmcnr_unit_width(mosfet_dict, _state.topology_config, _state.I_ref, W_cap)
            # 非首轮：L/gm_id 未变的管子保留上一轮 W。重算 W=Ids/idW 本身没错，但黄金 W 并非由本表算出，表里 (L,gm_id) 对应的 idW 算出的 W 可能与黄金不一致；且首轮 idW 在黄金覆盖前查表，与覆盖后 (L,gm_id) 可能不对应。故未改动的管沿用上轮 W 以保持工作点。
            if k >= 1 and _state.history:
                last_params = _state.history[-1].get("params") or {}
                for dev_name, dev in mosfet_dict.items():
                    curr_s = current_sizing.get(dev_name) or current_sizing.get(dev_name.upper()) or current_sizing.get(dev_name.lower())
                    prev = last_params.get(dev_name) or last_params.get(dev_name.upper()) or last_params.get(dev_name.lower())
                    if not isinstance(prev, dict) or prev.get("W") is None:
                        continue
                    if not isinstance(curr_s, dict):
                        continue
                    # 若本轮 L、gm_id 与上一轮相同，视为未改该管，保留上一轮 W
                    try:
                        l_ok = float(curr_s.get("L", 0)) == float(prev.get("L", 0))
                        g_ok = float(curr_s.get("gm_id", 0)) == float(prev.get("gm_id", 0))
                    except (TypeError, ValueError):
                        l_ok, g_ok = False, False
                    if l_ok and g_ok:
                        dev.update_param("W", float(prev["W"]))
            # 首轮（k==0）强制使用黄金 L/W/m，保证第一次仿真从可工作点开始，避免 W=ids/idW 算出异常（如 xm23 W=1000）导致 DC 全 0
            if k == 0:
                golden = _state.topology_config.get("nmcnr_golden_LW_um") or {}
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
                        mosfet_dict[key].update_param("m", mult_val)
                    # 内部 W 存总宽，与 update_W_result 一致；黄金为每指宽，故 W_total = W_per_finger * mult
                    if "W" in lw:
                        w_tot = float(lw["W"]) * max(1, mult_val)
                        mosfet_dict[key].update_param("W", round(w_tot, 4))
                print("  [NMCNR 首轮] 已用 topology 中 nmcnr_golden_LW_um 覆盖 L/W/m，与可工作点一致。")
                # 黄金覆盖后重查表，使 idW 与当前 (L,gm_id) 一致，后续迭代重算时同一 (L,gm_id) 得到同一 idW
                lookup_res = lookup_gmid_for_mosfet_dict(mosfet_dict, _state.LOOKUP_DIR)
                apply_lookup_to_mosfet_dict(mosfet_dict, lookup_res)
        
        # 打印物理参数：亲眼观察 W 随 gm_id 的跳变
        params = snapshot_params(mosfet_dict)
        print(f"  params ({len(mosfet_dict)} 管): {json.dumps(params, indent=2)}")

        # 执行仿真（包含旧文件清理逻辑）
        if _state.topology_config.get("bias_type") == "nmcnr":
            netlist = generate_netlist_nmcnr(mosfet_dict, topology_config=_state.topology_config)
            # 将网表中的基准电流从 3u 动态替换为 Agent1 计算得到的 I_ref，保持比例不变同时适配 Sky130 目标指标
            try:
                netlist = netlist.replace("CURRENT_0_BIAS=3u", f"CURRENT_0_BIAS={float(_state.I_ref)}u")
            except Exception:
                pass
            cir_name = "NMCNR"
        else:
            netlist = generate_netlist_ota5(mosfet_dict, voltage_source_dict, sl_mosfet_dict, sl_voltage_source_dict)
            cir_name = "AMP"
        run_simulation(netlist, cir_name, sim_files, _state.SIM_DIR)
        results = read_simulation_results(_state.SIM_DIR)
        
        # 计算 Gap 与 FOM
        gap = compute_gap(results, _state.specs)
        specs_met = is_specs_met(gap)
        fom = compute_fom(results, VDD) if specs_met else 0.0

        # 每轮都输出仿真结果单行摘要，避免被 params 大段 JSON 淹没
        g = results.get("Gain", 0)
        gbw = results.get("GBW", 0)
        pm = results.get("PM", 0)
        sr = results.get("SR", 0)
        idc = results.get("i(vmeas)", 0)
        print(f"  >>> 仿真结果: Gain={g:.2f} dB, GBW={gbw:.2f} MHz, PM={pm:.2f} °, SR={sr:.2f} V/µs, IDC={idc:.2f} µA")
        print(f"  >>> gap: {gap}")
        try:
            sys.stdout.flush()
        except Exception:
            pass

        if specs_met and fom > _state.best_fom:
            _state.best_fom, _state.best_params, _state.best_results, _state.best_iter = fom, params, results, k
            print(f"  ✓ 满足要求！FOM = {fom:.6f}")

        # 记录历史记录
        _state.history.append({"iter": k, "params": params, "results": results, "gap": gap, "specs_met": specs_met, "fom": fom})
        
        print(f"  results: {results}")
        print(f"  gap: {gap}")
        current_sizing = None

    return _state.history

if __name__ == "__main__":
    user_args = {"GBW_min": 100, "PM_min": 60, "Gain_min": 60, "CL": 2}
    prompt_file = "sys0_sleeve.txt"
    if len(sys.argv) > 1:
        try:
            if sys.argv[1].startswith('{'): user_args = json.loads(sys.argv[1])
            else: prompt_file = sys.argv[1]
        except: pass
    if len(sys.argv) > 2: prompt_file = sys.argv[2]
    run(user_args, netlist_prompt=prompt_file)