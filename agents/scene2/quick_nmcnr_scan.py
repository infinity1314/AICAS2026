import os
import sys
import json

"""
quick_nmcnr_scan.py

用途：
- 快速在 NMCNR 三级运放上测试一组给定的 L / gm_id 尺寸，跑一次仿真看 Gain 是否大于 0。
- 不走完整多轮 Agent2/Agent3 流程，只执行一轮 sizing → 查表 → 生成网表 → ngspice 仿真。

用法示例（在项目根目录运行）：

  python scene2/quick_nmcnr_scan.py

然后在本文件内修改 SIZING_NMCNR 中各个 xm* 的 L / gm_id，再重新运行即可快速查看 Gain/GBW/PM。
"""

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)

if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import state as _state  # 本地 scene2/state

from scene1.data import (
    mosfet_dict, voltage_source_dict,
)
from scene2.topology_config import get_topology_config
from scene2.agents.utils import (
    read_sys_prompt,
    parse_netlist,
    apply_sizing_to_mosfet_dict,
    update_ids_result,
    lookup_gmid_for_mosfet_dict,
    apply_lookup_to_mosfet_dict,
    update_vb_result,
    update_W_result,
    apply_nmcnr_unit_width,
    generate_netlist_nmcnr,
    run_simulation,
    read_simulation_results,
    read_operating_point,
)
from scene2.agents.calc_params import compute_design_params


# 一组可快速修改的 NMCNR 初始尺寸（L / gm_id），与实测可工作参数一致（Gain~95dB, GBW~16.8MHz, PM~62°）
# 仅对“镜像同尺寸”的管子做 sharing；xm4/xm11 等按电流独立算 W。
SIZING_NMCNR = {
    # ---- 第一级：输入对 ----
    "xm8":  {"L": 2.0, "gm_id": 8.2},
    "xm9":  {"L": 2.0, "gm_id": 8.2},

    # ---- PMOS 偏置链（xm0 为 Master，xm4 尾电流独立）----
    "xm0":  {"L": 2.0, "gm_id": 8.25},
    "xm4":  {"L": 2.0, "gm_id": 7.85},
    "xm5":  {"L": 2.0, "gm_id": 10.44},
    "xm6":  {"L": 2.0, "gm_id": 10.44},
    "xm7":  {"L": 2.0, "gm_id": 8.24},

    # ---- 第二级 ----
    "xm10": {"L": 1.45, "gm_id": 10.66},
    "xm21": {"L": 1.5, "gm_id": 17.53},
    "xm22": {"L": 1.5, "gm_id": 17.35},

    # ---- 第三级：输出管 gm_id 偏低以增大 W、利于饱和 ----
    "xm23": {"L": 2.51, "gm_id": 3.1},
    "xm11": {"L": 2.0, "gm_id": 8.15},

    # ---- NMOS 偏置链（xm14 为 Master）----
    "xm14": {"L": 1.5, "gm_id": 12.2},
    "xm12": {"L": 1.5, "gm_id": 17.95},
    "xm13": {"L": 1.5, "gm_id": 17.95},
    "xm15": {"L": 1.5, "gm_id": 19.96},
    "xm16": {"L": 1.5, "gm_id": 19.96},
    "xm17": {"L": 1.5, "gm_id": 15.16},
    "xm18": {"L": 1.5, "gm_id": 15.16},
    "xm19": {"L": 1.5, "gm_id": 16.35},
    "xm20": {"L": 1.5, "gm_id": 16.35},
}
def _to_upper_keys(d: dict) -> dict:
    """将 xm* 小写键名转换为 XM*，以匹配 mosfet_dict 的键。"""
    return {k.upper(): v for k, v in d.items()}


def main():
    # 1) 选择 NMCNR 拓扑 & 仿真文件
    netlist_prompt = "sys0_NMCNR.txt"
    topo_cfg = get_topology_config(netlist_prompt)
    _state.topology_config = topo_cfg
    _state.SIM_DIR = os.path.join(_THIS_DIR, topo_cfg.get("sim_subdir", "sim_NMCNR"))
    _state.LOOKUP_DIR = os.path.join(_THIS_DIR, "lookup_tables")

    # 2) 读取 sys0_NMCNR.txt 网表并解析到 mosfet_dict
    mosfet_dict.clear()
    voltage_source_dict.clear()
    content = read_sys_prompt(netlist_prompt).get("content", "")
    parse_netlist(mosfet_dict, voltage_source_dict, content)

    # 3) 设定一组规格，用于计算 I_ref / Itail / scale2/3
    #    这里直接用主流程同一套默认：GBW_min=100, SR_min=80, PM_min=60, CL=2, IDC_max=600
    specs_in = {
    "GBW_min": 50,   # 先降一半
    "SR_min": 40,    # 先减半
    "PM_min": 60,
    "Gain_min": 40,  # 起点只要求 >40dB
    "IDC_max": 600,
    "CL": 2,
    "I_ref": 5.0,
    

}
    design_params = compute_design_params(specs_in, topology_type="nmcnr")
    _state.Itail = float(design_params.get("Itail", 80.0))
    # 用黄金 L/W 覆盖时，网表 I0 必须与黄金一致（5u），否则电流过大导致 Vds 塌陷、多管不饱和
    use_golden = bool(topo_cfg.get("nmcnr_golden_LW_um"))
    if use_golden:
        _state.I_ref = float(topo_cfg.get("nmcnr_I_ref", 5.0))
        _state.Itail = 4.0 * _state.I_ref
        print("设计参数（compute_design_params 原始输出，仅供参考）：")
        print(json.dumps(design_params, indent=2, ensure_ascii=False))
        print(f"黄金 L/W 模式：网表偏置 I0 = {_state.I_ref} uA（与可工作点一致）；Itail={_state.Itail} uA")
    else:
        _state.I_ref = float(design_params.get("I_ref", 20.0))
        print("设计参数（compute_design_params 原始输出，仅供参考）：")
        print(json.dumps(design_params, indent=2, ensure_ascii=False))
        print(f"I_ref = {_state.I_ref} uA, Itail = {_state.Itail} uA")

    # 4) 使用当前文件中的 SIZING_NMCNR 作为尺寸（L / gm_id），并在本脚本中显式执行 NMCNR 的物理同步链路
    sizing_upper = _to_upper_keys(SIZING_NMCNR)

    # A. 写入初始 L / gm_id
    apply_sizing_to_mosfet_dict(mosfet_dict, sizing_upper)

    # B. 按 NMCNR current_groups / scale2/3 分配电流
    update_ids_result(
        mosfet_dict,
        _state.Itail,
        tail_device=topo_cfg.get("tail_device"),
        topology_config={**topo_cfg, "I_ref": _state.I_ref},
    )

    # C. 查表得到 idW（gm/id → Id/W）
    lookup_res = lookup_gmid_for_mosfet_dict(mosfet_dict, _state.LOOKUP_DIR)
    apply_lookup_to_mosfet_dict(mosfet_dict, lookup_res)

    # D. 刷新偏置（NMCNR 分支会直接 return，不改偏置源）
    update_vb_result(
        mosfet_dict,
        voltage_source_dict,
        VDD=1.8,
        VSS=0.0,
        VCM=0.9,
        bias_type="nmcnr",
        topology_config=topo_cfg,
    )

    # E. 先按 Ids/idW 计算一次 W，并应用 NMCNR 的 W 上限
    W_cap = float(topo_cfg.get("nmcnr_W_max_um")) if topo_cfg.get("nmcnr_W_max_um") is not None else None
    update_W_result(mosfet_dict, W_max_um=W_cap)

    # F. NMCNR：仅做 parameter_sharing（W/L 从 Master 拷到 Slave），W 保持上面 update_W_result 的 ids/idW
    apply_nmcnr_unit_width(mosfet_dict, topo_cfg, _state.I_ref, W_cap)

    # F0. 调试：用黄金/论文电流比例时，查表 W = ids/idW 与黄金 W（每指宽）对比
    golden = topo_cfg.get("nmcnr_golden_LW_um") or {}
    compare = {}
    for name, dev in sorted(mosfet_dict.items(), key=lambda x: int(x[0][2:]) if x[0][2:].isdigit() else 999):
        W_calc = dev.get_param("W")
        g = golden.get(name.lower()) or golden.get(name.upper()) or golden.get(name)
        if isinstance(g, dict) and "W" in g:
            try:
                compare[name] = {
                    "W_from_lookup": round(float(W_calc), 4),
                    "W_golden": float(g["W"]),
                }
            except Exception:
                continue
    if compare:
        print("\n=== 黄金/论文电流比例下 查表 W 与黄金 W 对比（单位: um，每指宽）===")
        print(json.dumps(compare, indent=2, ensure_ascii=False))

    # F'. 若配置了可工作点黄金 L/W/mult，直接覆盖（W 为每指宽、mult 与 .PARAM 一致），网表输出与手工可工作 NMCNR.cir 一致
    if golden:
        for name, lw in golden.items():
            key = name if name in mosfet_dict else (name.upper() if name.upper() in mosfet_dict else None)
            if key and isinstance(lw, dict):
                if "L" in lw:
                    mosfet_dict[key].update_param("L", float(lw["L"]))
                mult_val = int(lw["mult"]) if lw.get("mult") is not None else 1
                if "mult" in lw:
                    mosfet_dict[key].update_param("m", float(mult_val))
                if "W" in lw:
                    w_tot = float(lw["W"]) * max(1, mult_val)
                    mosfet_dict[key].update_param("W", round(w_tot, 4))
        print("(已使用 topology 中 nmcnr_golden_LW_um 覆盖 L/W/mult，与可工作点 .PARAM 一致)")

    # G. 生成网表并仿真（显式替换基准电流 CURRENT_0_BIAS）
    netlist = generate_netlist_nmcnr(mosfet_dict, topology_config=topo_cfg)
    try:
        netlist = netlist.replace("CURRENT_0_BIAS=3u", f"CURRENT_0_BIAS={float(_state.I_ref)}u")
    except Exception:
        pass

    # 只跑 IDC + AC，暂时跳过 SR bench，先让 DC/AC 出结果
    sim_files = ["NMCNR_IDC_pdk.cir", "NMCNR_AC_pdk.cir"]
    run_simulation(netlist, "NMCNR", sim_files, _state.SIM_DIR)

    results = read_simulation_results(_state.SIM_DIR)
    device_names = sorted(mosfet_dict.keys(), key=lambda x: int(x[2:]) if x[2:].isdigit() else 999)
    device_op = read_operating_point(_state.SIM_DIR, device_names, mosfet_dict)

    print("\n=== 一次仿真结果 (NMCNR) ===")
    print("AC / SR 指标:")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    print("\n各管 DC 工作点（是否饱和）:")
    print(json.dumps(device_op, indent=2, ensure_ascii=False))

    if results.get("Gain", 0.0) > 0:
        print("\n>>> Gain 已经大于 0，可以作为进一步优化的起点。")
    else:
        print("\n>>> Gain 仍然 ≤ 0，需要继续调整 SIZING_NMCNR 中的 L / gm_id。")


if __name__ == "__main__":
    main()

