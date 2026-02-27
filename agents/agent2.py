import os
import sys
import json

_agents = os.path.dirname(os.path.abspath(__file__))
_scene1 = os.path.dirname(_agents)
_root = os.path.dirname(_scene1)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scene2.agents.utils import read_sys_prompt, read_human_prompt

# --- 角色范围定义 (全量保留：涵盖所有放大级、负载及偏置支路角色) ---
ROLE_RANGES = {
    "输入对管": {"L_min": 0.15, "L_max": 0.5, "gm_id_min": 10, "gm_id_max": 20},
    "负载管": {"L_min": 0.25, "L_max": 1.15, "gm_id_min": 10, "gm_id_max": 15}, 
    "共源共栅_上臂": {"L_min": 0.5, "L_max": 1.15, "gm_id_min": 8, "gm_id_max": 14},
    "共源共栅_下臂": {"L_min": 0.4, "L_max": 1.2, "gm_id_min": 6, "gm_id_max": 18},
    "尾电流管": {"L_min": 0.5, "L_max": 1.5, "gm_id_min": 10, "gm_id_max": 20},
    "第二级_放大管": {"L_min": 0.4, "L_max": 1.0, "gm_id_min": 10, "gm_id_max": 20},
    "第三级_放大管": {"L_min": 0.4, "L_max": 1.0, "gm_id_min": 10, "gm_id_max": 20},
    "负载电流源": {"L_min": 0.6, "L_max": 1.5, "gm_id_min": 8, "gm_id_max": 16},
    "偏置管": {"L_min": 0.6, "L_max": 1.5, "gm_id_min": 8, "gm_id_max": 18},
}
DEFAULT_RANGE = {"L_min": 0.2, "L_max": 1.0, "gm_id_min": 8, "gm_id_max": 20}

FALLBACK_DEVICE_ROLES = {
    "XM1": "输入对管", "XM2": "输入对管", "XM3": "负载管", "XM4": "负载管",
    "XM5": "共源共栅_下臂", "XM6": "共源共栅_下臂", "XM7": "共源共栅_上臂", "XM8": "共源共栅_上臂", "XM9": "尾电流管",
}

class ConstraintAgent:
    """根据拓扑分类适配：高性能套筒式模式 vs NMCNR 强力救活模式。"""

    # --- 19dB 套筒式备用黄金参数 ---
    GOLDEN_SIZING = {
        "xm11": {"L": 0.9, "gm_id": 8.5}, "xm7":  {"L": 0.9, "gm_id": 8.5},
        "xm10": {"L": 0.9, "gm_id": 8.5}, "xm6":  {"L": 0.9, "gm_id": 8.5},
        "xm5":  {"L": 0.9, "gm_id": 8.5}, "xm9":  {"L": 0.9, "gm_id": 8.5},
        "xm8":  {"L": 0.9, "gm_id": 8.5}, "xm4":  {"L": 0.6, "gm_id": 14.0},
        "xm3":  {"L": 0.9, "gm_id": 8.5}, "xm2":  {"L": 0.9, "gm_id": 8.5},
        "xm1":  {"L": 0.9, "gm_id": 8.5}, "xm0":  {"L": 0.9, "gm_id": 8.5},
        "xm23": {"L": 0.6, "gm_id": 14.0}, "xm22": {"L": 0.6, "gm_id": 14.0},
        "xm21": {"L": 0.6, "gm_id": 14.0}, "xm19": {"L": 0.6, "gm_id": 14.0},
        "xm15": {"L": 0.6, "gm_id": 14.0}, "xm20": {"L": 0.6, "gm_id": 14.0},
        "xm16": {"L": 0.6, "gm_id": 14.0}, "xm17": {"L": 0.6, "gm_id": 14.0},
        "xm14": {"L": 0.6, "gm_id": 14.0}, "xm12": {"L": 0.6, "gm_id": 14.0},
        "xm18": {"L": 0.6, "gm_id": 14.0}, "xm13": {"L": 0.6, "gm_id": 14.0},
    }

    # --- NMCNR Gemini 救活参数全量锁定 (全 22 管镜像链) ---
    GEMINI_NMCNR_SIZING = {
        "xm4":  {"L": 1.5, "gm_id": 10.0}, "xm8":  {"L": 1.5, "gm_id": 12.0},
        "xm9":  {"L": 1.5, "gm_id": 12.0}, "xm5":  {"L": 1.5, "gm_id": 10.0},
        "xm6":  {"L": 1.5, "gm_id": 10.0}, "xm7":  {"L": 1.5, "gm_id": 8.5}, 
        "xm10": {"L": 1.5, "gm_id": 8.5}, "xm21": {"L": 1.5, "gm_id": 19.5},
        "xm22": {"L": 1.5, "gm_id": 19.5}, "xm11": {"L": 1.0, "gm_id": 8.5},
        "xm23": {"L": 1.0, "gm_id": 20.0},
        "xm0":  {"L": 1.5, "gm_id": 12.0}, "xm1":  {"L": 1.5, "gm_id": 12.0},
        "xm2":  {"L": 1.5, "gm_id": 12.0}, "xm3":  {"L": 1.5, "gm_id": 12.0},
        "xm12": {"L": 1.5, "gm_id": 12.0}, "xm13": {"L": 1.5, "gm_id": 12.0},
        "xm14": {"L": 1.5, "gm_id": 12.0}, "xm15": {"L": 1.5, "gm_id": 12.0},
        "xm16": {"L": 1.5, "gm_id": 12.0}, "xm17": {"L": 1.5, "gm_id": 12.0},
        "xm18": {"L": 1.5, "gm_id": 12.0}, "xm19": {"L": 1.5, "gm_id": 12.0},
        "xm20": {"L": 1.5, "gm_id": 12.0}
    }

    PROTECTED_DEVICES = ("xm5", "xm6", "xm7", "xm10")
    NMCNR_INPUT_PAIR = ("xm8", "xm9")
    NMCNR_STAGE3_NO_SAT_ADJUST = ("xm11", "xm23")

    def __init__(self):
        self.messages = []
        _os = __import__("os")
        self.client = None
        if _os.getenv("DASHSCOPE_API_KEY"):
            from openai import OpenAI
            self.client = OpenAI(
                api_key=_os.getenv("DASHSCOPE_API_KEY"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

    @classmethod
    def _initial_sizing(cls, constraints: dict, topology_config: dict = None) -> dict:
        """分类初始化：NMCNR 注入救活参数，套筒式回归中点探索。"""
        bias_type = (topology_config or {}).get("bias_type")
        is_nmcnr = (bias_type == "nmcnr")
        
        sizing = {}
        for d, c in constraints.items():
            d_lookup = d.lower()
            if is_nmcnr and d_lookup in cls.GEMINI_NMCNR_SIZING:
                L = cls.GEMINI_NMCNR_SIZING[d_lookup]["L"]
                g = cls.GEMINI_NMCNR_SIZING[d_lookup]["gm_id"]
            else:
                L = (float(c["L_min"]) + float(c["L_max"])) / 2
                g = (float(c["gm_id_min"]) + float(c["gm_id_max"])) / 2
            sizing[d] = {"L": round(L, 3), "gm_id": round(g, 2)}
        return sizing

    @staticmethod
    def _constraints_from_roles(topology: dict, specs: dict = None, fallback_roles: dict = None, topology_config: dict = None) -> dict:
        devices = topology.get("devices") or []
        roles = topology.get("device_roles") or {}
        fallback = fallback_roles if isinstance(fallback_roles, dict) and fallback_roles else FALLBACK_DEVICE_ROLES
        constraints = {}
        for d in devices:
            role = roles.get(d) or fallback.get(d)
            r = ROLE_RANGES.get(role, DEFAULT_RANGE) if isinstance(role, str) else DEFAULT_RANGE
            constraints[d] = dict(r)
        
        # 恢复 Overrides 逐行判定逻辑
        overrides = (topology_config or {}).get("role_gm_id_overrides") or {}
        for d in devices:
            role = roles.get(d) or fallback.get(d)
            if isinstance(role, str) and role in overrides:
                for k, v in (overrides[role] or {}).items():
                    if k in constraints[d] and v is not None:
                        if "min" in k: 
                            constraints[d][k] = max(constraints[d][k], float(v))
                        else: 
                            constraints[d][k] = min(constraints[d][k], float(v))
        
        if specs:
            for key in ["L_min", "L_max", "gm_id_min", "gm_id_max"]:
                val = specs.get(key)
                if val is not None:
                    for d, c in constraints.items():
                        if "min" in key: 
                            c[key] = max(float(c[key]), float(val))
                        else: 
                            c[key] = min(float(c[key]), float(val))
        return constraints

    def _adjust_for_linear_llm(self, sizing: dict, constraints: dict, device_op: dict, topology: dict, bias_type: str) -> dict:
        if not self.client:
            return self._adjust_for_linear(sizing, constraints, device_op)
        
        linear = [d for d, info in (device_op or {}).items() if not info.get("saturation")]
        if not linear: return sizing

        try:
            sys_sat = read_sys_prompt("sys2_saturation.txt")
            human_sat = read_human_prompt(
                "human2_saturation.txt",
                current_sizing_json=json.dumps(sizing, indent=2),
                device_op_json=json.dumps(device_op, indent=2),
                linear_devices=json.dumps(linear),
                constraints_json=json.dumps(constraints, indent=2),
                device_roles_json=json.dumps(topology.get("device_roles") or {}, ensure_ascii=False),
            )
            completion = self.client.chat.completions.create(
                model="qwen-plus", messages=[sys_sat, human_sat], temperature=0.0, response_format={"type": "json_object"}
            )
            raw = json.loads(completion.choices[0].message.content or "{}")
            new_params = raw.get("sizing") or raw
            
            next_sizing = dict(sizing)
            for d, old_val in sizing.items():
                d_lower = d.lower()
                # 强力锁定：NMCNR 下禁止 LLM 修改黄金管
                if bias_type == "nmcnr" and d_lower in self.PROTECTED_DEVICES and d_lower in self.GOLDEN_SIZING:
                    continue 
                
                if d in new_params:
                    c = constraints[d]
                    L_raw = float(new_params[d].get("L", old_val["L"]))
                    g_raw = float(new_params[d].get("gm_id", old_val["gm_id"]))
                    L = max(float(c["L_min"]), min(float(c["L_max"]), L_raw))
                    g = max(float(c["gm_id_min"]), min(float(c["gm_id_max"]), g_raw))
                    next_sizing[d] = {"L": round(L, 3), "gm_id": round(g, 2)}
            return next_sizing
        except Exception as e:
            print(f"  [LLM 饱和调节失败] 触发物理步进回退: {e}")
            return self._adjust_for_linear(sizing, constraints, device_op)

    @staticmethod
    def _adjust_for_linear(sizing: dict, constraints: dict, device_op: dict) -> dict:
        next_sizing = dict(sizing)
        for d, info in (device_op or {}).items():
            if d not in sizing or info.get("saturation"): continue
            c = constraints[d]
            L_new = min(float(c["L_max"]), sizing[d]["L"] * 1.05)
            next_sizing[d] = {"L": round(L_new, 3), "gm_id": sizing[d]["gm_id"]}
        return next_sizing

    @classmethod
    def _enforce_nmcnr_input_pair_symmetry(cls, sizing: dict, topology_config: dict) -> dict:
        if not topology_config or topology_config.get("bias_type") != "nmcnr":
            return sizing
        a, b = cls.NMCNR_INPUT_PAIR[0], cls.NMCNR_INPUT_PAIR[1]
        if a not in sizing or b not in sizing or not isinstance(sizing[a], dict) or not isinstance(sizing[b], dict):
            return sizing
        L_sym = max(sizing[a].get("L", 1.0), sizing[b].get("L", 1.0))
        g_sym = round((sizing[a].get("gm_id", 15.0) + sizing[b].get("gm_id", 15.0)) / 2.0, 2)
        sizing[a].update({"L": round(L_sym, 3), "gm_id": g_sym})
        sizing[b].update({"L": round(L_sym, 3), "gm_id": g_sym})
        return sizing

    def invoke(self, specs: dict, topology: dict, simulate_fn=None, topology_config: dict = None) -> dict:
        devices = topology.get("devices") or []
        if not devices: return {}

        cfg = topology_config or {}
        bias_type = cfg.get("bias_type", "telescopic")
        constraints = self._constraints_from_roles(topology, specs=specs, fallback_roles=cfg.get("fallback_roles"), topology_config=cfg)
        sizing = self._initial_sizing(constraints, topology_config=cfg)
        sizing = self._enforce_nmcnr_input_pair_symmetry(sizing, cfg)
        
        print(f"  [Agent2 模式切换] 拓扑分析：{bias_type}")

        out = {"devices": constraints, "_initial_sizing": sizing}

        if callable(simulate_fn):
            max_iter = 10 
            for it in range(max_iter):
                run_out = simulate_fn(sizing)
                device_op = run_out.get("device_op") or {}
                if not device_op: 
                    print("  Agent2: OP 解析中断。")
                    break
                
                cas_list = [d for d in devices if d.lower() in self.PROTECTED_DEVICES]
                cascode_sat = all(device_op.get(d, {}).get("saturation", False) for d in cas_list)
                all_sat = all(info.get("saturation") for info in device_op.values())
                
                if all_sat:
                    print(f"  Agent2: 第 {it+1} 轮仿真已达全饱和。")
                    break
                if bias_type == "nmcnr" and cascode_sat and it >= 1:
                    print("  Agent2: 核心 Cascode 已饱和，停止迭代以锁定稳定性。")
                    break

                linear = [d for d, info in device_op.items() if not info.get("saturation")]
                if bias_type == "nmcnr":
                    linear = [d for d in linear if d not in self.NMCNR_INPUT_PAIR and d not in self.NMCNR_STAGE3_NO_SAT_ADJUST]
                
                print(f"  Agent2 饱和迭代 {it+1}: 未饱和管 {linear}")
                
                device_op_adj = device_op
                if bias_type == "nmcnr":
                    skip = set(self.NMCNR_INPUT_PAIR) | set(self.NMCNR_STAGE3_NO_SAT_ADJUST)
                    device_op_adj = {k: v for k, v in device_op.items() if k not in skip}
                
                sizing = self._adjust_for_linear_llm(sizing, constraints, device_op_adj, topology, bias_type)
                sizing = self._enforce_nmcnr_input_pair_symmetry(sizing, cfg)
            
            out["_initial_sizing"] = self._enforce_nmcnr_input_pair_symmetry(sizing, cfg)

        return out

if __name__ == "__main__":
    a = ConstraintAgent()
    print("Agent2 准备就绪，逻辑核对无误。")