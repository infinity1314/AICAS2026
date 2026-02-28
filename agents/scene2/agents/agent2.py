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
# 说明：
# - 这些范围需要同时兼容套筒式与 NMCNR 三级放大器。
# - 之前为输入对管/二级/三级设置的 L_max=0.5/1.0 会与 NMCNR 黄金 L=2.0/2.5 等产生严重冲突，
#   导致 Agent3 认为“几何约束被违反”而一次性把 L 从 2.0 砍到 0.45，引发 DC 崩溃。
# - 这里将范围放宽到覆盖黄金参数附近，使 Agent2/Agent3 在「合理物理区间」内工作，不再和黄金点对着干。
ROLE_RANGES = {
    # 输入级：同时兼容套筒 (L≈0.3) 与 NMCNR (L≈2.0)
    "输入对管": {"L_min": 0.15, "L_max": 2.5, "gm_id_min": 6, "gm_id_max": 20},
    # 套筒负载 & NMCNR 中的一部分 PMOS 负载
    "负载管": {"L_min": 0.25, "L_max": 2.0, "gm_id_min": 6, "gm_id_max": 18}, 
    # 共源共栅上/下臂：L 稍放宽，gm_id 范围适中
    "共源共栅_上臂": {"L_min": 0.5, "L_max": 2.0, "gm_id_min": 6, "gm_id_max": 18},
    "共源共栅_下臂": {"L_min": 0.4, "L_max": 2.0, "gm_id_min": 6, "gm_id_max": 18},
    # 尾电流管：L 稍长以便安全裕量
    "尾电流管": {"L_min": 0.5, "L_max": 2.0, "gm_id_min": 6, "gm_id_max": 20},
    # 第二级/第三级放大管：L 覆盖 0.4~2.5，gm_id 允许更宽，避免限制 NMCNR 黄金值
    "第二级_放大管": {"L_min": 0.4, "L_max": 2.0, "gm_id_min": 6, "gm_id_max": 22},
    "第三级_放大管": {"L_min": 0.4, "L_max": 2.5, "gm_id_min": 3, "gm_id_max": 20},
    # NMCNR 中的各类电流源/偏置支路
    "负载电流源": {"L_min": 0.6, "L_max": 2.0, "gm_id_min": 6, "gm_id_max": 18},
    "偏置管": {"L_min": 0.6, "L_max": 2.0, "gm_id_min": 4, "gm_id_max": 18},
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

    # --- NMCNR 初始参数：与实测可工作网表一致（Gain~95dB, GBW~16.8MHz, PM~62°）---
    GEMINI_NMCNR_SIZING = {
        "xm8":  {"L": 2.0, "gm_id": 8.2},  "xm9":  {"L": 2.0, "gm_id": 8.2},
        "xm0":  {"L": 2.0, "gm_id": 8.25}, "xm1":  {"L": 2.0, "gm_id": 8.19},
        "xm2":  {"L": 2.0, "gm_id": 8.19}, "xm3":  {"L": 2.0, "gm_id": 8.20},
        "xm4":  {"L": 2.0, "gm_id": 7.85}, "xm5":  {"L": 2.0, "gm_id": 10.44},
        "xm6":  {"L": 2.0, "gm_id": 10.44}, "xm7":  {"L": 2.0, "gm_id": 8.24},
        "xm10": {"L": 1.45, "gm_id": 10.66}, "xm11": {"L": 2.0, "gm_id": 8.15},
        "xm12": {"L": 1.5, "gm_id": 17.95}, "xm13": {"L": 1.5, "gm_id": 17.95},
        "xm14": {"L": 1.5, "gm_id": 12.2},  "xm15": {"L": 1.5, "gm_id": 19.96},
        "xm16": {"L": 1.5, "gm_id": 19.96}, "xm17": {"L": 1.5, "gm_id": 15.16},
        "xm18": {"L": 1.5, "gm_id": 15.16}, "xm19": {"L": 1.5, "gm_id": 16.35},
        "xm20": {"L": 1.5, "gm_id": 16.35}, "xm21": {"L": 1.5, "gm_id": 17.53},
        "xm22": {"L": 1.5, "gm_id": 17.35}, "xm23": {"L": 2.51, "gm_id": 3.1},
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
    def _enforce_nmcnr_input_pair_symmetry(cls, sizing: dict, topology_config: dict) -> dict:
        if not topology_config or topology_config.get("bias_type") != "nmcnr":
            return sizing
            
        # [新增] 强制 Slave 完全继承 Master 的 L 和 gm_id
        sharing = topology_config.get("parameter_sharing", {})
        if sharing:
            for slave, master in sharing.items():
                if slave in sizing and master in sizing:
                    sizing[slave]["L"] = sizing[master].get("L", sizing[slave]["L"])
                    sizing[slave]["gm_id"] = sizing[master].get("gm_id", sizing[slave]["gm_id"])
                    
        # 保持原有的输入对对称逻辑兜底
        a, b = cls.NMCNR_INPUT_PAIR[0], cls.NMCNR_INPUT_PAIR[1]
        if a in sizing and b in sizing and isinstance(sizing[a], dict) and isinstance(sizing[b], dict):
            L_sym = max(sizing[a].get("L", 1.0), sizing[b].get("L", 1.0))
            g_sym = round((sizing[a].get("gm_id", 15.0) + sizing[b].get("gm_id", 15.0)) / 2.0, 2)
            sizing[a].update({"L": round(L_sym, 3), "gm_id": g_sym})
            sizing[b].update({"L": round(L_sym, 3), "gm_id": g_sym})
            
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

    def _initial_sizing(self, constraints: dict, topology_config: dict = None) -> dict:
        """根据拓扑给出初始 L/gm_id：NMCNR 用 GEMINI_NMCNR_SIZING，套筒式用约束中点（XM7/XM8 取 L_max、gm_id_min 便于饱和）。"""
        cfg = topology_config or {}
        sizing = {}
        if cfg.get("bias_type") == "nmcnr":
            golden = self.GEMINI_NMCNR_SIZING
            for d in constraints:
                g = golden.get(d) or golden.get(d.lower())
                if isinstance(g, dict) and "L" in g and "gm_id" in g:
                    sizing[d] = {"L": round(float(g["L"]), 3), "gm_id": round(float(g["gm_id"]), 2)}
                else:
                    c = constraints[d]
                    L = (float(c["L_min"]) + float(c["L_max"])) / 2
                    g = (float(c["gm_id_min"]) + float(c["gm_id_max"])) / 2
                    sizing[d] = {"L": round(L, 3), "gm_id": round(g, 2)}
        else:
            for d, c in constraints.items():
                L_min, L_max = float(c["L_min"]), float(c["L_max"])
                g_min, g_max = float(c["gm_id_min"]), float(c["gm_id_max"])
                if d in ("XM7", "XM8"):
                    L, g = L_max, g_min
                else:
                    L, g = (L_min + L_max) / 2, (g_min + g_max) / 2
                sizing[d] = {"L": round(L, 3), "gm_id": round(g, 2)}
        return sizing

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
                # OP 失效原因：本轮 sizing 下仿真 DC 不收敛或 ngspice 报错，.control 未执行完，
                # Op_1.txt 未写入或未从 cwd 拷入 sim_dir，read_operating_point 读不到有效数据返回 {}。
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