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
            current_sizing = {key: v for key, v in raw.items() if key in device_names and isinstance(v, dict)}
            # NMCNR 防暴走：强制 L >= nmcnr_L_min_um，避免 Agent3 把 L 砍到 0.25 导致 DC 不收敛、后续迭代 AC 全 0
            cfg = _state.topology_config or {}
            if cfg.get("bias_type") == "nmcnr":
                L_min = float(cfg.get("nmcnr_L_min_um") or 0.6)
                for _dev, p in current_sizing.items():
                    if isinstance(p, dict) and "L" in p and p.get("L") is not None:
                        try: p["L"] = max(float(p["L"]), L_min)
                        except (TypeError, ValueError): pass
                    if isinstance(p, dict) and "gm_id" in p and p.get("gm_id") is not None:
                        try: p["gm_id"] = max(0.1, min(float(p["gm_id"]), 25.0))
                        except (TypeError, ValueError): pass
                # 输入对 xm8/xm9 必须对称，否则差分对不对称导致 GBW/Gain 异常
                if "xm8" in current_sizing and "xm9" in current_sizing and isinstance(current_sizing["xm8"], dict) and isinstance(current_sizing["xm9"], dict):
                    L8, g8 = current_sizing["xm8"].get("L"), current_sizing["xm8"].get("gm_id")
                    L9, g9 = current_sizing["xm9"].get("L"), current_sizing["xm9"].get("gm_id")
                    L_sym = max(float(L8 or 0), float(L9 or 0))
                    g_sym = round((float(g8 or 0) + float(g9 or 0)) / 2.0, 2)
                    current_sizing["xm8"] = {"L": round(L_sym, 3), "gm_id": g_sym}
                    current_sizing["xm9"] = {"L": round(L_sym, 3), "gm_id": g_sym}
                print(f"  [NMCNR 防护] L 已钳位 >= {L_min} µm，gm_id 已钳位 [0.1, 25]")
        
        if not current_sizing: continue

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
        
        # 打印物理参数：亲眼观察 W 随 gm_id 的跳变
        params = snapshot_params(mosfet_dict)
        print(f"  params ({len(mosfet_dict)} 管): {json.dumps(params, indent=2)}")

        # 执行仿真（包含旧文件清理逻辑）
        if _state.topology_config.get("bias_type") == "nmcnr":
            netlist = generate_netlist_nmcnr(mosfet_dict)
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