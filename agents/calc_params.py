"""
Agent1 Fallback 工具：OTA 设计参数预估（支持多拓扑）。

作用：
- 当 Agent1 没有给出可靠的 scale / Itail 等数值时，提供一个物理合理的保底初始化；
- 区分折叠式 / NMCNR 等拓扑，对二级、三级电流比例给出默认值。

约定单位：
- GBW_min: MHz
- SR_min:  V/us
- PM_min:  deg
- CL:      pF
- I_ref:   uA
"""

import math


def compute_design_params(input_specs: dict, topology_type: str | None = None) -> dict:
    """
    OTA 设计参数预估：
    1. scale1 / Itail：基于 SR 与 CL 计算第一级尾电流倍数。
    2. scale2 / scale3：
       - 折叠/套筒：按文档公式给出 scale2_min，并对 scale2 取 max(user_scale2, scale2_min)；
       - NMCNR：默认 scale2 = 1.0, scale3 = 6.0（除非用户/Agent1 显式给正值）。
    """
    # --- 1. 基础指标提取与保底 ---
    GBW_min = _float(input_specs.get("GBW_min"), 50.0)
    SR_min = _float(input_specs.get("SR_min"), 80.0)
    PM_min = _float(input_specs.get("PM_min"), 60.0)
    CL = _float(input_specs.get("CL"), 2.0)  # pF，默认 2pF，防止 CL 缺失导致 Itail 爆炸
    
    # 对 NMCNR 给一个更“强攻型”的默认电流参考
    if (topology_type or "").lower() == "nmcnr":
        I_ref = _float(input_specs.get("I_ref"), 20.0)  # uA
    else:
        I_ref = _float(input_specs.get("I_ref"), 100.0)  # uA

    out: dict = {
        "GBW_min": GBW_min,
        "SR_min": SR_min,
        "PM_min": PM_min,
        "CL": CL,
        "I_ref": I_ref,
        "Gain_min": _float(input_specs.get("Gain_min"), 60.0),
        "IDC_max": _float(input_specs.get("IDC_max"), 600.0),
    }

    # --- 2. Scale1 / Itail 计算 ---
    # 物理公式: Itail ≈ SR * CL，scale1 = (SR_min * CL) / I_ref（无量纲）
    user_scale1 = input_specs.get("scale1")
    scale1_computed = (SR_min * CL) / max(I_ref, 1e-6)

    if user_scale1 is not None:
        s = user_scale1 if isinstance(user_scale1, str) else str(user_scale1).strip()
        # Agent1 可能返回公式字符串如 "30.0 / I_ref"，不能直接 float()
        if s and ("I_ref" in s or "/" in s or "*" in s or " " in s):
            try:
                # 安全求值：仅替换 I_ref 为数值后计算
                safe_expr = s.replace("I_ref", str(I_ref)).replace(" ", "")
                scale1 = float(eval(safe_expr))
            except Exception:
                scale1 = scale1_computed
        else:
            try:
                scale1 = float(user_scale1)
            except (ValueError, TypeError):
                scale1 = scale1_computed
    else:
        scale1 = scale1_computed

    # 确保 scale1 至少为 1.0（Itail >= I_ref）
    scale1 = max(scale1, 1.0)
    Itail = round(scale1 * I_ref, 4)
    # 套筒式：限定尾电流上限 160µA（单支 80µA），避免 Agent1 返回过大 scale1 导致 300/600
    topo = (topology_type or "").lower()
    if topo not in ("nmcnr", "three_stage"):
        Itail = min(Itail, 160.0)
        scale1 = Itail / max(I_ref, 1e-6)
    out["scale1"] = round(scale1, 4)
    out["Itail"] = round(Itail, 4)

    # --- 3. Scale2 / Scale3：根据拓扑类型区分 ---
    is_folded = topo not in ("nmcnr", "three_stage")

    # 预提取用户给出的比例
    user_scale2 = _float(input_specs.get("scale2"), 0.0)
    user_scale3 = _float(input_specs.get("scale3"), 0.0)

    if is_folded:
        # 文档公式：二级电流倍数下限
        gm_id_casc = 15.0  # A/V
        GBW_rad = GBW_min * 1e6 * 2.0 * math.pi
        C_fold = 0.15 * CL * 1e-12
        I_ref_A = I_ref * 1e-6
        angle = max(1e-3, (90.0 - PM_min) * math.pi / 180.0)
        tan_term = math.tan(angle)

        try:
            scale2_min = 2.2 * GBW_rad * C_fold * tan_term / (gm_id_casc * max(I_ref_A, 1e-12))
        except Exception:
            scale2_min = 0.0
        
        scale2_min = max(scale2_min, 0.0)
        out["scale2_min"] = round(scale2_min, 4)

        base_s2 = user_scale2 if user_scale2 > 0 else scale2_min
        out["scale2"] = round(max(base_s2, scale2_min), 4)
        out["scale3"] = 0.0 # 折叠式没有第三级
    else:
        # 三级 NMCNR 核心保护逻辑
        out["scale2_min"] = None
        if topo == "nmcnr":
            # I1:I2:I3 比例逻辑
            if user_scale2 > 0:
                s2 = user_scale2
            else:
                s2 = 1.0  # 默认 I2 ≈ I_ref
            
            if user_scale3 > 0:
                # 实施 Gemini 诊断建议的上限保护
                s3 = min(user_scale3, 8.0) 
            else:
                s3 = 6.0  # 默认比例，防止输出级贫血或过载
            
            out["scale2"] = round(s2, 4)
            out["scale3"] = round(s3, 4)
        else:
            out["scale2"] = round(user_scale2, 4)
            out["scale3"] = round(user_scale3, 4)

    return out


def _float(v, default=None):
    """鲁棒的浮点数转换工具。"""
    if v is None:
        return default
    try:
        # 处理可能传入的带单位字符串或数学表达式的简单转换
        if isinstance(v, str):
            # 仅移除可能存在的单位，不执行复杂的 eval 避免安全风险
            v_clean = v.replace("u", "").replace("p", "").replace("M", "").strip()
            return float(v_clean)
        return float(v)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    # 简单自测
    test_in = {"GBW_min": 100, "SR_min": 80, "CL": 5, "I_ref": 10}
    print("折叠式 Fallback:", compute_design_params(test_in, topology_type="folded"))
    print("NMCNR Fallback:", compute_design_params(test_in, topology_type="nmcnr"))