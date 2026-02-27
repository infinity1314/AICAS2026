#!/usr/bin/env python3
"""
折叠式单独仿真：只跑一次折叠共源共栅 OTA 的 AC（或 AC+IDC），ngspice 输出写入单独 log，不被主流程覆盖。

用法（在项目根目录）:
  python scene2/run_folded_standalone.py
  python scene2/run_folded_standalone.py params.json

可选环境变量:
  SCENE1_USE_PDK=1  使用 PDK（folded_AC_pdk.cir），否则用 folded_AC.cir
  FOLDED_LOG=folded_standalone.log  log 文件名（默认 folded_standalone.log）
  FOLDED_RUN_IDC=1  同时跑 IDC（会追加到同一 log 或单独 log）

Log 始终写在 scene2/sim/<FOLDED_LOG>，与主程序的 log 分离。
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
)

_state.SIM_DIR = os.path.join(_scene1, "sim")
_state.LOOKUP_DIR = os.path.join(_scene1, "lookup_tables")
_state.Itail = 120.0
VDD, VSS, VCM = 1.8, 0.0, 0.9

NETLIST_PROMPT = "sys0_folded.txt"
# 折叠式 11 管默认 L/gm_id（仅用于单独仿真，便于看 log）
DEFAULT_FOLDED_PARAMS = {
    "M_tail": {"L": 1.25, "gm_id": 14.0},
    "M_in_p": {"L": 0.325, "gm_id": 17.5},
    "M_in_n": {"L": 0.325, "gm_id": 17.5},
    "M_load_p": {"L": 1.125, "gm_id": 16.5},
    "M_load_n": {"L": 1.125, "gm_id": 16.5},
    "M_casc_p_p": {"L": 1.0, "gm_id": 11.5},
    "M_casc_p_n": {"L": 1.0, "gm_id": 11.5},
    "M_casc_n_p": {"L": 1.0, "gm_id": 11.5},
    "M_casc_n_n": {"L": 1.0, "gm_id": 11.5},
    "M_cmfb_p": {"L": 0.625, "gm_id": 13.0},
    "M_cmfb_n": {"L": 0.625, "gm_id": 13.0},
}


def load_params(path):
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "params" in data:
            raw = data["params"]
        else:
            raw = {k: v for k, v in data.items() if isinstance(v, dict) and ("L" in v or "gm_id" in v)}
        return {k: {"L": v.get("L"), "gm_id": v.get("gm_id")} for k, v in raw.items() if isinstance(v, dict) and (v.get("L") is not None or v.get("gm_id") is not None)}
    return dict(DEFAULT_FOLDED_PARAMS)


def main():
    params_path = sys.argv[1] if len(sys.argv) > 1 else None
    sizing = load_params(params_path)
    if params_path and os.path.isfile(params_path):
        print("使用参数文件:", params_path)
    else:
        print("使用脚本内折叠式默认参数")

    sim_dir = _state.SIM_DIR
    lookup_dir = _state.LOOKUP_DIR
    topology_config = get_topology_config(NETLIST_PROMPT)
    sys_prompt = read_sys_prompt(NETLIST_PROMPT)
    content = sys_prompt.get("content", "")
    mosfet_dict.clear()
    voltage_source_dict.clear()
    parse_netlist(mosfet_dict, voltage_source_dict, content)
    device_names = list(mosfet_dict.keys())
    sizing = {k: v for k, v in sizing.items() if k in device_names}
    if not sizing:
        print("错误: 没有与折叠网表匹配的参数，需要:", device_names)
        sys.exit(1)

    use_pdk = os.environ.get("SCENE1_USE_PDK", "1").strip().lower() in ("1", "true", "yes")
    log_name = os.environ.get("FOLDED_LOG", "folded_standalone.log").strip() or "folded_standalone.log"
    run_idc = os.environ.get("FOLDED_RUN_IDC", "0").strip().lower() in ("1", "true", "yes")

    if use_pdk:
        sim_files = ["folded_AC_pdk.cir"]
        if run_idc:
            sim_files.append("folded_IDC_pdk.cir")
    else:
        sim_files = ["folded_AC.cir"]
        if run_idc:
            sim_files.append("folded_IDC.cir")

    apply_sizing_to_mosfet_dict(mosfet_dict, sizing)
    update_ids_result(mosfet_dict, _state.Itail, tail_device=topology_config.get("tail_device"))
    lookup_result = lookup_gmid_for_mosfet_dict(mosfet_dict, lookup_dir=lookup_dir)
    apply_lookup_to_mosfet_dict(mosfet_dict, lookup_result)
    update_vb_result(mosfet_dict, voltage_source_dict, VDD, VSS, VCM, bias_type=topology_config.get("bias_type"), lookup_dir=lookup_dir, topology_config=topology_config)
    update_W_result(mosfet_dict)

    netlist = generate_netlist_ota5(mosfet_dict, voltage_source_dict, use_pdk_subckt=use_pdk)
    os.makedirs(sim_dir, exist_ok=True)
    with open(os.path.join(sim_dir, "AMP.cir"), "w") as f:
        f.write(netlist)

    log_path = os.path.abspath(os.path.join(sim_dir, log_name))
    cwd = os.getcwd()
    # PDK 的 .cir 里是 .include 'scene2/sim/AMP.cir'，必须从项目根跑才能找到；非 PDK 在 sim_dir 下跑、用 .include 'AMP.cir'
    run_dir = _root if use_pdk else sim_dir
    sim_rel = os.path.relpath(sim_dir, _root)  # e.g. scene2/sim
    try:
        os.chdir(run_dir)
        for i, f in enumerate(sim_files):
            cir_path = os.path.join(sim_rel, f) if use_pdk else f
            if not os.path.exists(cir_path):
                with open(log_path, "a" if i else "w", encoding="utf-8") as logf:
                    logf.write("File not found: {}\n".format(cir_path))
                continue
            with open(log_path, "a" if i else "w", encoding="utf-8") as logf:
                logf.write("======== ngspice -b {} ========\n".format(cir_path))
            ret = os.system("ngspice -b {} >> {} 2>&1".format(cir_path, log_path))
            if ret != 0:
                with open(log_path, "a", encoding="utf-8") as logf:
                    logf.write("(exit code {})\n".format(ret))
    finally:
        os.chdir(cwd)

    print("折叠式单独仿真完成。Log 已写入: {}".format(log_path))
    print("查看: cat {} 或 less {}".format(log_path, log_path))


if __name__ == "__main__":
    main()
