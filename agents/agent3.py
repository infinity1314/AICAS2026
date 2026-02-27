"""
Agent3: 迭代 sizing。每轮先量化分析历史中的 action/param_change 与 observation_impact（result_delta、gap_delta），
分析当前观察量与需求的关系，再思考并输出本轮的 (L, gm_id) action。仅引用 scene2/prompt。
"""
import os
import sys
import json

_agents = os.path.dirname(os.path.abspath(__file__))
_scene2 = os.path.dirname(_agents)
_root = os.path.dirname(_scene2)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scene2.agents.utils import read_sys_prompt, read_human_prompt, is_specs_met, compute_fom, W_MAX_UM

# 套筒拓扑：管子 -> 组名，用于判断「上一轮是否多组同改」
_DEVICE_GROUP = {
    "XM1": "input", "XM2": "input",
    "XM3": "load", "XM4": "load",
    "XM5": "upper_cascode", "XM6": "upper_cascode",
    "XM7": "lower_cascode", "XM8": "lower_cascode",
    "XM9": "tail",
}


def _groups_changed(param_change: dict) -> set:
    """param_change 中涉及的角色组集合。"""
    if not param_change:
        return set()
    return {_DEVICE_GROUP.get(dev, dev) for dev in param_change if _DEVICE_GROUP.get(dev)}


def _is_collapse(entry: dict, w_max_um: float = None) -> bool:
    """判定该轮是否为「参数崩溃」：指标全 0 或某管 W 异常过大。NMCNR 时传入 w_max_um=30 更严。"""
    if not entry:
        return False
    results = entry.get("results") or {}
    if (float(results.get("Gain") or 0) == 0 and float(results.get("GBW") or 0) == 0
            and float(results.get("PM") or 0) == 0):
        return True
    threshold = float(w_max_um) if w_max_um is not None else W_MAX_UM
    params = entry.get("params") or {}
    for _dev, p in params.items():
        if isinstance(p, dict):
            w = p.get("W")
            if w is not None and float(w) >= threshold:
                return True
    return False


class SizingAgent:
    """constraints + history -> 下一轮每管 (L, gm_id)。"""

    def __init__(self):
        self.messages = []
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        _os = __import__("os")
        self.client = None
        if _os.getenv("DASHSCOPE_API_KEY"):
            from openai import OpenAI
            self.client = OpenAI(
                api_key=_os.getenv("DASHSCOPE_API_KEY"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

    def _completion(self, messages, response_format=None):
        """调用 API：response_format=None 时自由文本，否则为 JSON。"""
        if self.client is None:
            ref = {"XM1": (0.2, 20), "XM2": (0.2, 20), "XM3": (0.85, 14), "XM4": (0.85, 14),
                   "XM5": (0.85, 7.5), "XM6": (0.85, 7.5), "XM7": (0.85, 7.5), "XM8": (0.85, 7.5), "XM9": (0.9, 11)}
            return type("R", (), {"content": json.dumps({d: {"L": L, "gm_id": g} for d, (L, g) in ref.items()})})()
        kwargs = {"temperature": 0.0, "model": "qwen-plus", "messages": messages}
        if response_format is not None:
            kwargs["response_format"] = response_format
        completion = self.client.chat.completions.create(**kwargs)
        msg = completion.choices[0].message
        if msg.content is None:
            msg.content = "{}"
        return msg

    def get_response(self, messages):
        """兼容旧用法：强制 JSON 输出。"""
        return self._completion(messages, response_format={"type": "json_object"})

    def invoke(self, constraints: dict, history: list, device_names: list, topology: dict = None, specs: dict = None, itail: float = None, idc_max: float = None, topology_config: dict = None) -> dict:
        sys3 = read_sys_prompt("sys3_reason.txt")
        roles = (topology or {}).get("device_roles") or (topology or {}).get("roles") or {}
        specs_dict = specs or {}
        itail = float(itail) if itail is not None else float(specs_dict.get("Itail") or specs_dict.get("I_ref") or 100)
        idc_max = float(idc_max) if idc_max is not None else float(specs_dict.get("IDC_max") or 500)
        # 当前焦点：未达标项优先；若满足要求则以 FOM 为优化目标
        last_entry = history[-1] if history else {}
        last_gap = last_entry.get("gap") or {}
        last_results = last_entry.get("results") or {}
        specs_met = last_entry.get("specs_met", False) if history else False

        # 如果上一轮已满足要求，检查 FOM
        if not specs_met and history:
            specs_met = is_specs_met(last_gap)

        undersatisfied = []
        for k in ("Gain", "GBW", "PM"):
            v = last_gap.get(k)
            if v is not None and v < 0:
                undersatisfied.append((k, v))
        if last_gap.get("SR") is not None and last_gap["SR"] < 0:  # [SR-as-spec]
            undersatisfied.append(("SR", last_gap["SR"]))
        if last_gap.get("IDC") is not None and last_gap["IDC"] > 0:
            undersatisfied.append(("IDC", last_gap["IDC"]))
        
        if specs_met:
            # 满足要求：以 FOM 为优化目标
            current_fom = last_entry.get("fom", 0) if history else 0
            if current_fom == 0 and last_results:
                # 如果 history 中没有 fom，重新计算
                current_fom = compute_fom(last_results)
            best_fom = max((h.get("fom", 0) for h in history if h.get("specs_met")), default=0)
            gap_pm = last_gap.get("PM")
            current_focus = "✓ 当前所有指标已达标！\n"
            current_focus += "当前 FOM = {:.6f}，历史最佳 FOM = {:.6f}。\n".format(current_fom, best_fom)
            current_focus += "本轮优化目标：在保持指标达标的前提下，最大化 FOM = (GBW_MHz × Gain_linear) / Power_μW。\n"
            current_focus += "优化策略：在约束范围内适度调整参数，提高 GBW 和 Gain，同时降低功耗（IDC），以提升 FOM。\n"
            # PM 余量极小时：不指定具体管子，由模型根据推导判断“会损 PM”的调整并避免
            if gap_pm is not None and 0 < float(gap_pm) <= 1.0:
                current_focus += "\n【提示】当前 PM 余量极小（gap.PM = {:.4f}°）。请根据你对架构与极点/相位裕度的推导，避免任何会显著降低 PM 的调整；仅可做不损 PM 的微调或保持当前参数。\n".format(float(gap_pm))
            current_focus += "\n"
        elif not undersatisfied:
            current_focus = "当前所有指标已达标（但 specs_met 标志未设置）。\n\n"
        else:
            parts = [f"{k}(gap={v})" for k, v in undersatisfied]
            current_focus = "当前未达标项（本轮优先改善）：" + "，".join(parts) + "。\n"
            # 仅 PM 未达标：不指定具体管子，由模型根据推导+历史选择参数；可提示一步到位
            only_pm = len(undersatisfied) == 1 and undersatisfied[0][0] == "PM"
            gap_pm_val = undersatisfied[0][1] if only_pm else None
            if only_pm and gap_pm_val is not None and -2.0 <= gap_pm_val < 0:
                current_focus += "（仅 PM 未达标：请根据架构与历史推导对 PM 影响最大的单类参数；若历史中单组调整显示有效，可一步到位以减少迭代。）\n"
            if any(abs(v) < 0.3 for _, v in undersatisfied):
                current_focus += "存在 |gap| < 0.3 的未达标项，已进入微调阶段：对该类项仅做小幅 L/gm_id 调整；其余未达标项仍按正常步幅调整。\n\n"
            else:
                current_focus += "\n"
        # 若上一轮同时改了多组参数，提示 result_delta 归因不唯一，本轮建议单组调整
        last_param_change = last_entry.get("param_change") or {}
        groups = _groups_changed(last_param_change)
        if len(groups) >= 2:
            current_focus += "【归因提示】上一轮同时调整了多组参数（涉及：{}），该轮的 result_delta 无法唯一归因到某一参数。分析时请以历史中「仅单组调整」的轮次为准做因果判断。\n\n".format("、".join(sorted(groups)))

        # 若上一轮为「参数崩溃」，则要求以崩溃前一轮为基准并更换优化方向
        collapse_by_history = False
        if len(history) >= 2:
            # 规则：若任一指标的 gap 相比上一轮「朝恶化方向」变化超过 30，也视为调崩
            # Gain/GBW/PM/SR：gap 变得更负超过 30
            # IDC：gap 变得更正超过 30（功耗超限变严重）
            prev_gap = history[-2].get("gap") or {}
            try:
                for k in ("Gain", "GBW", "PM", "SR"):
                    if k in last_gap and k in prev_gap:
                        cur = float(last_gap.get(k) or 0)
                        prev = float(prev_gap.get(k) or 0)
                        if cur < prev - 30.0:
                            collapse_by_history = True
                            break
                if not collapse_by_history and "IDC" in last_gap and "IDC" in prev_gap:
                    cur_idc = float(last_gap.get("IDC") or 0)
                    prev_idc = float(prev_gap.get("IDC") or 0)
                    if cur_idc > prev_idc + 30.0:
                        collapse_by_history = True
            except (TypeError, ValueError):
                collapse_by_history = collapse_by_history

        cfg = topology_config or {}
        # NMCNR 无单独 W 上限时用全局 W_MAX_UM 判定崩溃
        w_collapse = float(cfg.get("nmcnr_W_max_um")) if (cfg.get("bias_type") == "nmcnr" and cfg.get("nmcnr_W_max_um") is not None) else None
        if len(history) >= 2 and (_is_collapse(last_entry, w_max_um=w_collapse) or collapse_by_history):
            baseline_entry = history[-2]
            baseline_params = baseline_entry.get("params") or {}
            # 只给 (L, gm_id)，便于模型直接以此为基准
            baseline_summary = {d: {"L": p.get("L"), "gm_id": p.get("gm_id")}
                               for d, p in baseline_params.items() if isinstance(p, dict)}
            collapse_focus = "【崩溃回退】上一轮调参导致仿真崩溃（指标 Gain/GBW/PM 全 0、某管 W 触及上限，或某一指标 gap 恶化超过 30）。\n"
            collapse_focus += "本轮请以「崩溃前一轮」的参数为基准输出本轮的 (L, gm_id)，即你的输出 = 在崩溃前一轮参数基础上做**一次新的、更换方向的调整**。\n"
            collapse_focus += "勿重复上一轮导致崩溃的调整：例如若上一轮为增加负载管(XM3/XM4) L 或某管触及约束边界导致 W 爆炸，则本轮禁止再沿该方向调整该管；请换其他管子或反向调整。\n"
            collapse_focus += "崩溃前一轮（作为本轮基准）的参数摘要：{}\n".format(
                json.dumps(baseline_summary, ensure_ascii=False, indent=2))
            if last_entry.get("param_change"):
                collapse_focus += "上一轮变动过的器件与方向：{}\n".format(
                    json.dumps(last_entry["param_change"], ensure_ascii=False, indent=2))
            current_focus = collapse_focus + "\n" + current_focus

        human3 = read_human_prompt(
            "human3.txt",
            roles_json=json.dumps(roles, indent=2, ensure_ascii=False),
            specs_json=json.dumps(specs_dict, indent=2, ensure_ascii=False),
            constraints_json=json.dumps(constraints, indent=2),
            history_json=json.dumps(history, indent=2, ensure_ascii=False),
            current_focus=current_focus,
            itail=itail,
            idc_max=idc_max,
        )
        messages_reason = [sys3, human3]
        if self.client is None:
            # 无 API 时按当前拓扑 device_names 与 constraints 中点生成默认 action
            dev_c = (constraints.get("devices") or constraints) if isinstance(constraints.get("devices"), dict) else {}
            out = {"think": "(无 API，使用约束中点)"}
            for d in device_names:
                r = dev_c.get(d) if isinstance(dev_c, dict) else {}
                if isinstance(r, dict) and (r.get("L_min") is not None or r.get("L_max") is not None):
                    L = (float(r.get("L_min", 0.3)) + float(r.get("L_max", 0.8))) / 2
                    g = (float(r.get("gm_id_min", 8)) + float(r.get("gm_id_max", 18))) / 2
                    out[d] = {"L": round(L, 3), "gm_id": round(g, 2)}
                else:
                    out[d] = {"L": 0.4, "gm_id": 12}
            return out

        # 阶段 1：专家推理（结构化 JSON：baseline_comparison, sensitivity_analysis, incremental_calculation, reasoning_summary）
        msg_reason = self._completion(messages_reason, response_format={"type": "json_object"})
        reasoning_text = (msg_reason.content or "").strip()
        try:
            stage1_json = json.loads(reasoning_text)
        except json.JSONDecodeError:
            stage1_json = {}
        # 日志中的「推理原文」= 完整结构化推理（含灵敏度矩阵）
        think_for_log = json.dumps(stage1_json, ensure_ascii=False, indent=2)

        # 阶段 2：从 incremental_calculation 抽取 (L, gm_id)，并做约束截断
        sys3_format = read_sys_prompt("sys3_format.txt")
        constraints_dev = constraints.get("devices") if isinstance(constraints.get("devices"), dict) else constraints
        format_input = {
            "stage1_reasoning_json": stage1_json,
            "device_list": device_names,
            "constraints": constraints_dev if isinstance(constraints_dev, dict) else constraints,
        }
        msg_format = self._completion(
            [sys3_format, {"role": "user", "content": json.dumps(format_input, ensure_ascii=False, indent=2)}],
            response_format={"type": "json_object"},
        )
        try:
            sizing_dict = json.loads(msg_format.content or "{}")
        except json.JSONDecodeError:
            sizing_dict = {}

        # 若阶段二未返回完整器件表，从阶段一 incremental_calculation 兜底抽取 XM*_L、XM*_gm_id
        inc = (stage1_json.get("incremental_calculation") or {}) if isinstance(stage1_json, dict) else {}
        for d in device_names:
            if d not in sizing_dict or not isinstance(sizing_dict.get(d), dict):
                L_val = inc.get("{}_L".format(d))
                g_val = inc.get("{}_gm_id".format(d))
                if L_val is not None and g_val is not None:
                    try:
                        sizing_dict[d] = {"L": float(L_val), "gm_id": float(g_val)}
                    except (TypeError, ValueError):
                        pass

        out = {"think": think_for_log}
        for d in device_names:
            if d in sizing_dict and isinstance(sizing_dict[d], dict):
                out[d] = {k: sizing_dict[d][k] for k in ("L", "gm_id") if k in sizing_dict[d]}
        return out


if __name__ == "__main__":
    a = SizingAgent()
    constraints = {"devices": {"M_tail": {"L_min": 0.2, "L_max": 0.6, "gm_id_min": 8, "gm_id_max": 18}}}
    history = [{"params": {}, "results": {"Gain": 55, "GBW": 40}, "gap": {"Gain": -5, "GBW": -10}}]
    out = a.invoke(constraints, history, ["M_tail", "M_in_p"])
    print(json.dumps(out, indent=2))
