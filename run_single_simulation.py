"""
scene1 下单次仿真：用给定参数跑一次 AC+IDC，结果写入 sim/single_simulation_result.json。
不调用任何 Agent，仅做：网表加载 → 应用 L/gm_id → 查表算 W → 仿真 → 读结果 → 写文件。

用法:
  python scene1/run_single_simulation.py [params.json]
  无参数时使用脚本内默认参数（套筒式 XM1–XM9）。
  params.json 格式: {"<device>": {"L": 0.4, "gm_id": 22}, ...}，器件名须与当前网表一致（套筒式 XM1–XM9）。
  或: {"params": {"XM1": {"L": 0.4, "gm_id": 22}, ...}}
"""
import os
import sys
import json

_scene1 = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_scene1)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scene1 import state as _state
from scene1.data import mosfet_dict, voltage_source_dict
from scene2.topology_config import get_topology_config
from scene2.agents.utils import (
    read_sys_prompt,
    parse_netlist,
    apply_sizing_to_mosfet_dict,
    update_ids_result,
    update_vb_result,
    lookup_gmid_for_mosfet_dict,
    apply_lookup_to_mosfet_dict,
    update_W_result,
    generate_netlist_ota5,
    run_simulation,
    read_simulation_results,
    compute_gap,
)

_state.SIM_DIR = os.path.join(_scene1, "sim")
_state.LOOKUP_DIR = os.path.join(_scene1, "lookup_tables")
_state.Itail = 120.0

VDD, VSS, VCM = 1.8, 0.0, 0.9

# 默认参数（与 main 输出中曾达标的一组一致：Gain≈60, GBW≈97, PM≈62, IDC≈118）
DEFAULT_PARAMS = {
    "XM1": {"L": 0.4, "gm_id": 22.0},
    "XM2": {"L": 0.4, "gm_id": 22.0},
    "XM3": {"L": 0.63, "gm_id": 12.0},
    "XM4": {"L": 0.63, "gm_id": 12.0},
    "XM5": {"L": 1.28, "gm_id": 6.0},
    "XM6": {"L": 1.28, "gm_id": 6.0},
    "XM7": {"L": 1.28, "gm_id": 6.0},
    "XM8": {"L": 1.28, "gm_id": 6.0},
    "XM9": {"L": 0.7, "gm_id": 10.5},
}


def load_params(path: str):
    """支持: main 的 history 条目 JSON（含 params）、或顶层 {"XM1": {L, gm_id}, ...}。只用 L、gm_id。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "params" in data:
        raw = data["params"]
    else:
        raw = {k: v for k, v in data.items() if isinstance(v, dict) and ("L" in v or "gm_id" in v)}
    return {k: {"L": v.get("L"), "gm_id": v.get("gm_id")} for k, v in raw.items() if isinstance(v, dict) and (v.get("L") is not None or v.get("gm_id") is not None)}


def main():
    params_path = sys.argv[1] if len(sys.argv) > 1 else None
    if params_path and os.path.isfile(params_path):
        sizing = load_params(params_path)
        print("使用参数文件:", params_path)
    else:
        sizing = dict(DEFAULT_PARAMS)
        print("使用脚本内默认参数")

    netlist_prompt = os.environ.get("SCENE1_NETLIST", "sys0_sleeve.txt").strip()
    if not netlist_prompt.endswith(".txt"):
        netlist_prompt = netlist_prompt + ".txt"
    topology_config = get_topology_config(netlist_prompt)
    sys_prompt = read_sys_prompt(netlist_prompt)
    content = sys_prompt.get("content", "")
    mosfet_dict.clear()
    voltage_source_dict.clear()
    parse_netlist(mosfet_dict, voltage_source_dict, content)
    device_names = list(mosfet_dict.keys())
    sizing = {k: v for k, v in sizing.items() if k in device_names}
    if not sizing:
        print("错误: 没有与网表器件名匹配的参数（当前网表: {}，器件: {}；套筒式为 XM1–XM9）".format(netlist_prompt, device_names))
        sys.exit(1)

    lookup_dir = _state.LOOKUP_DIR
    sim_dir = _state.SIM_DIR
    use_pdk = os.environ.get("SCENE1_USE_PDK", "1").strip().lower() in ("1", "true", "yes")
    sim_files = topology_config.get("sim_files_pdk" if use_pdk else "sim_files_compact")
    if not sim_files:
        sim_files = ["AMP_AC_pdk.cir", "AMP_IDC_pdk.cir"] if use_pdk else ["AMP_AC.cir", "AMP_IDC.cir"]
    print("拓扑:", topology_config.get("label", netlist_prompt), "| 仿真模式:", "PDK" if use_pdk else "紧凑模型 (建议设 SCENE1_USE_PDK=1)")

    apply_sizing_to_mosfet_dict(mosfet_dict, sizing)
    update_ids_result(mosfet_dict, _state.Itail, tail_device=topology_config.get("tail_device"))
    lookup_result = lookup_gmid_for_mosfet_dict(mosfet_dict, lookup_dir=lookup_dir)
    apply_lookup_to_mosfet_dict(mosfet_dict, lookup_result)
    update_vb_result(mosfet_dict, voltage_source_dict, VDD, VSS, VCM, bias_type=topology_config.get("bias_type"), lookup_dir=lookup_dir, topology_config=topology_config)
    update_W_result(mosfet_dict)

    # 套筒式不使用 PDK subckt
    netlist = generate_netlist_ota5(mosfet_dict, voltage_source_dict, use_pdk_subckt=False)
    run_simulation(netlist, cir_name="AMP", sim_files=sim_files, sim_dir=sim_dir)
    results = read_simulation_results(sim_dir=sim_dir)
    if (results.get("Gain", 0) == 0 and results.get("GBW", 0) == 0 and results.get("PM", 0) == 0):
        print("警告: AC 结果全为 0，可能仿真失败，请查看 {}/log 或确认 SCENE1_USE_PDK 与 PDK 路径。".format(sim_dir))

    specs = {
        "Gain_min": 60, "GBW_min": 20, "PM_min": 60,
        "IDC_max": 500,
    }
    gap = compute_gap(results, specs)

    out = {
        "params_used": sizing,
        "results": results,
        "gap": gap,
    }
    out_path = os.path.join(sim_dir, "single_simulation_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("仿真结果已写入:", out_path)
    print("results:", results)
    print("gap:", gap)


if __name__ == "__main__":
    main()
