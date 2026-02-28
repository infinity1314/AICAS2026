#!/usr/bin/env python3
"""
用「2.5 的倍数电流(KCL) + 黄金 L/gm_id + 现有表查表 W」跑一次 NMCNR 仿真，不覆盖黄金 W。
电流来自 topology 中 nmcnr_actual_ids_ua（全为 2.5 的倍数且满足 KCL）；网表 I0=nmcnr_I0_ua。
若仿真结果较正常，再生成 fixed-Ids 新表效果会更好。

用法（项目根）: python scene2/try_nmcnr_table_only.py
"""
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import state as _state
from scene1.data import mosfet_dict, voltage_source_dict
from scene2.topology_config import get_topology_config
from scene2.agents.utils import (
    read_sys_prompt,
    parse_netlist,
    update_ids_result,
    lookup_gmid_for_mosfet_dict,
    apply_lookup_to_mosfet_dict,
    update_vb_result,
    update_W_result,
    apply_nmcnr_unit_width,
    generate_netlist_nmcnr,
    run_simulation,
    read_simulation_results,
)


def main():
    netlist_prompt = "sys0_NMCNR.txt"
    topo_cfg = get_topology_config(netlist_prompt)
    _state.topology_config = topo_cfg
    _state.SIM_DIR = os.path.join(_THIS_DIR, topo_cfg.get("sim_subdir", "sim_NMCNR"))
    _state.LOOKUP_DIR = os.path.join(_THIS_DIR, "lookup_tables")
    # 用 KCL 表时网表 I0 必须为 net013 流入电流之和，用 nmcnr_I0_ua
    _state.I_ref = float(topo_cfg.get("nmcnr_I0_ua", topo_cfg.get("nmcnr_I_ref", 5.0)))
    _state.Itail = float(topo_cfg.get("nmcnr_actual_ids_ua", {}).get("xm4", 20.0))

    mosfet_dict.clear()
    voltage_source_dict.clear()
    content = read_sys_prompt(netlist_prompt).get("content", "")
    parse_netlist(mosfet_dict, voltage_source_dict, content)

    golden_lw = topo_cfg.get("nmcnr_golden_LW_um") or {}
    golden_gmid = topo_cfg.get("nmcnr_golden_gmid") or {}
    if not golden_gmid:
        print("未配置 nmcnr_golden_gmid，请先在 topology_config 中填写黄金 gm_id。")
        sys.exit(1)

    # 1) 黄金 L、黄金 gm_id（不写黄金 W，W 由查表得到）
    for name, g in golden_lw.items():
        key = name if name in mosfet_dict else (name.upper() if name.upper() in mosfet_dict else None)
        if not key or not isinstance(g, dict):
            continue
        if "L" in g:
            mosfet_dict[key].update_param("L", float(g["L"]))
        if "mult" in g:
            mosfet_dict[key].update_param("m", int(g["mult"]))
    for name, gid in golden_gmid.items():
        key = name if name in mosfet_dict else (name.upper() if name.upper() in mosfet_dict else None)
        if key:
            mosfet_dict[key].update_param("gmid", float(gid))

    # 2) 电流：优先 nmcnr_actual_ids_ua（2.5 的倍数且满足 KCL），否则按 current_groups 比例
    update_ids_result(
        mosfet_dict,
        _state.Itail,
        tail_device=topo_cfg.get("tail_device"),
        topology_config={**topo_cfg, "I_ref": _state.I_ref},
    )

    # 3) 查表 → idW，再 W = Ids/idW（不覆盖黄金 W）
    lookup_res = lookup_gmid_for_mosfet_dict(mosfet_dict, _state.LOOKUP_DIR)
    apply_lookup_to_mosfet_dict(mosfet_dict, lookup_res)
    update_vb_result(
        mosfet_dict, voltage_source_dict, VDD=1.8, VSS=0.0, VCM=0.9,
        bias_type="nmcnr", topology_config=topo_cfg,
    )
    W_cap = float(topo_cfg.get("nmcnr_W_max_um")) if topo_cfg.get("nmcnr_W_max_um") else None
    update_W_result(mosfet_dict, W_max_um=W_cap)
    apply_nmcnr_unit_width(mosfet_dict, topo_cfg, _state.I_ref, W_cap)

    # 4) 生成网表并仿真
    netlist = generate_netlist_nmcnr(mosfet_dict, topology_config=topo_cfg)
    netlist = netlist.replace("CURRENT_0_BIAS=3u", f"CURRENT_0_BIAS={float(_state.I_ref)}u")
    sim_files = ["NMCNR_IDC_pdk.cir", "NMCNR_AC_pdk.cir"]
    run_simulation(netlist, "NMCNR", sim_files, _state.SIM_DIR)
    results = read_simulation_results(_state.SIM_DIR)

    gain = results.get("Gain", 0.0)
    gbw_mhz = results.get("GBW", 0.0)  # read_simulation_results 中 GBW 已按 Hz 读入并除以 1e6，故为 MHz
    pm = results.get("PM", 0.0)
    print("\n=== 2.5 倍数电流(KCL) + 黄金 L/gm_id + 现有表查表 W（无黄金 W 覆盖）===")
    print(f"  Gain = {gain} dB,  GBW = {gbw_mhz} MHz,  PM = {pm} deg")
    if gain > 0 and gbw_mhz > 0:
        print("  仿真结果尚可。")
    else:
        print("  若结果不理想，可检查查表 W 或 fixed_ids 多表。")


if __name__ == "__main__":
    main()
