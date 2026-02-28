#!/usr/bin/env python3
"""
基于 scene2 的独立测试：将一组设计参数（gm_id, L, W, idW）带入网表与仿真环境，
输出 Gain、GBW、PM、i(vmeas)。使用 scene2 的 lookup_tables、sim 目录与 testbench。

用法（在项目根目录）:
  python -m scene2.run_test_sizing
  python -m scene2.run_test_sizing design.json

可选环境变量:
  SCENE1_USE_PDK=1 时使用 PDK 仿真文件。
"""
import os
import sys
import json

# 保证项目根在 path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 较好的一组参数（套筒式，与 agents 迭代 5 一致）
DEFAULT_DESIGN = {
    "XM1": {"gm_id": 22.591, "L": 0.25, "W": 129.88, "idW": 0.462},
    "XM2": {"gm_id": 22.591, "L": 0.25, "W": 129.88, "idW": 0.462},
    "XM3": {"gm_id": 15.6608, "L": 1.0, "W": 39.65, "idW": 1.5133},
    "XM4": {"gm_id": 15.6608, "L": 1.0, "W": 39.65, "idW": 1.5133},
    "XM5": {"gm_id": 7.9911, "L": 1.0, "W": 36.97, "idW": 1.6229},
    "XM6": {"gm_id": 7.9911, "L": 1.0, "W": 36.97, "idW": 1.6229},
    "XM7": {"gm_id": 7.9911, "L": 1.0, "W": 36.97, "idW": 1.6229},
    "XM8": {"gm_id": 7.9911, "L": 1.0, "W": 36.97, "idW": 1.6229},
    "XM9": {"gm_id": 12.2983, "L": 1.0, "W": 39.51, "idW": 3.0374},
}

VDD, VSS, VCM = 1.8, 0.0, 0.9


def load_design(path=None):
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(DEFAULT_DESIGN)


def main():
    design_path = sys.argv[1] if len(sys.argv) > 1 else None
    params = load_design(design_path)
    if not params:
        print("未加载到设计参数")
        return 1

    print("设计参数 (scene1 测试):", json.dumps(params, indent=2, ensure_ascii=False)[:500], "...")

    from scene1.data import mosfet_dict, voltage_source_dict
    from scene2.topology_config import get_topology_config
    from scene2.agents.utils import (
        read_sys_prompt,
        parse_netlist,
        update_ids_result,
        lookup_gmid_for_mosfet_dict,
        apply_lookup_to_mosfet_dict,
        update_vb_result,
        generate_netlist_ota5,
        run_simulation,
        read_simulation_results,
    )

    scene1_dir = os.path.join(ROOT, "scene1")
    sim_dir = os.path.join(scene1_dir, "sim")
    lookup_dir = os.path.join(scene1_dir, "lookup_tables")

    netlist_prompt = os.environ.get("SCENE1_NETLIST", "sys0_sleeve.txt").strip()
    if not netlist_prompt.endswith(".txt"):
        netlist_prompt = netlist_prompt + ".txt"
    topology_config = get_topology_config(netlist_prompt)
    tail_device = topology_config.get("tail_device")

    mosfet_dict.clear()
    voltage_source_dict.clear()
    sys_prompt = read_sys_prompt(netlist_prompt)
    content = sys_prompt.get("content", "")
    parse_netlist(mosfet_dict, voltage_source_dict, content)

    if not mosfet_dict:
        print("解析网表后未得到任何管子")
        return 1

    # Itail：从尾电流管 idW*W 推算（套筒式 XM9）
    tail_node = tail_device if tail_device and tail_device in params else "XM9"
    if tail_node and params.get(tail_node):
        pt = params[tail_node]
        Itail = float(pt.get("idW") or 0) * float(pt.get("W") or 0)
        if Itail <= 0:
            Itail = 120.0
    else:
        Itail = 120.0
    print("Itail (uA):", round(Itail, 2))

    tail = tail_device if tail_device and tail_device in mosfet_dict else next(iter(mosfet_dict.keys()), None)
    # 应用设计参数：L, W, gmid, idW, ids
    for name, m in mosfet_dict.items():
        p = params.get(name, {})
        L = float(p.get("L", 0.15))
        W = float(p.get("W", 1))
        gm_id = float(p.get("gm_id", 10))
        idw = float(p.get("idW", 1))
        m.update_param("L", L)
        m.update_param("W", W)
        m.update_param("gmid", gm_id)
        m.update_param("idW", idw)
        ids = Itail / 2 if name != tail else Itail
        m.update_param("ids", ids)

    # 查表得到 Vgs（用于偏置），保留当前 W/idW
    lookup_result = lookup_gmid_for_mosfet_dict(mosfet_dict, lookup_dir=lookup_dir)
    apply_lookup_to_mosfet_dict(mosfet_dict, lookup_result)
    # 恢复脚本给定的 W、idW，保证网表与输入一致
    for name, m in mosfet_dict.items():
        p = params.get(name, {})
        if p:
            m.update_param("W", float(p.get("W", 1)))
            m.update_param("idW", float(p.get("idW", 1)))

    update_vb_result(mosfet_dict, voltage_source_dict, VDD, VSS, VCM, bias_type=topology_config.get("bias_type"), lookup_dir=lookup_dir, topology_config=topology_config)

    # 默认用 PDK，否则简化模型易导致 Gain 为负、电流异常（与 agents 一致才得合理结果）
    use_pdk = os.environ.get("SCENE1_USE_PDK", "1").strip().lower() in ("1", "true", "yes")
    sim_files = topology_config.get("sim_files_pdk" if use_pdk else "sim_files_compact")
    if not sim_files:
        sim_files = ["AMP_AC_pdk.cir", "AMP_IDC_pdk.cir"] if use_pdk else ["AMP_AC.cir", "AMP_IDC.cir"]
    if use_pdk:
        print("仿真使用 PDK: models/sky130.lib.spice（与 agents 一致）")
    else:
        print("仿真使用 scene2/sim 默认模型（易出现负增益，建议设 SCENE1_USE_PDK=1）")

    # 套筒式不使用 PDK subckt
    netlist = generate_netlist_ota5(mosfet_dict, voltage_source_dict, use_pdk_subckt=False)
    run_simulation(netlist, cir_name="AMP", sim_files=sim_files, sim_dir=sim_dir)

    results = read_simulation_results(sim_dir=sim_dir)

    print("\n--- 仿真结果 (scene1) ---")
    for k in ["Gain", "GBW", "PM", "i(vmeas)"]:
        print("  {} = {}".format(k, results.get(k, "N/A")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
