"""
Agent0: 读取电路框架（网表），解析出拓扑与器件列表。仅引用 scene1 内 prompt。
"""
import os
import sys
import json

_agents = os.path.dirname(os.path.abspath(__file__))
_scene1 = os.path.dirname(_agents)
_root = os.path.dirname(_scene1)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scene2.agents.utils import read_sys_prompt, parse_netlist
from scene1.data import mosfet_dict, voltage_source_dict
from scene2.topology_config import get_topology_config


class CircuitAgent:
    """读取电路框架，解析网表，返回拓扑（器件列表）。"""

    def __init__(self):
        self.messages = []
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        import os as _os
        self.client = None
        if _os.getenv("DASHSCOPE_API_KEY"):
            from openai import OpenAI
            self.client = OpenAI(
                api_key=_os.getenv("DASHSCOPE_API_KEY"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

    def get_response(self, messages):
        if self.client is None:
            return type("R", (), {"content": json.dumps({"devices": list(mosfet_dict.keys())})})()
        completion = self.client.chat.completions.create(
            temperature=0.0,
            model="qwen-plus",
            messages=messages,
            response_format={"type": "json_object"},
        )
        msg = completion.choices[0].message
        if msg.content is None:
            msg.content = "{}"
        return msg

    def invoke(self, netlist_prompt_name: str = "sys0_sleeve.txt"):
        """从 prompt 读网表，解析拓扑与各管作用，返回 { "devices": [...], "device_roles": {...} }。"""
        sys0 = read_sys_prompt(netlist_prompt_name)
        content = sys0.get("content", "")
        mosfet_dict.clear()
        voltage_source_dict.clear()
        parse_netlist(mosfet_dict, voltage_source_dict, content)
        topology = {"devices": list(mosfet_dict.keys())}
        # 优先从网表注释解析各管作用（* Device roles: XM1,XM2 输入对管; ...）
        device_roles = {}
        for line in content.splitlines():
            line = line.strip()
            if "Device roles" in line or "device roles" in line.lower():
                if ":" in line:
                    _, rest = line.split(":", 1)
                else:
                    rest = line
                rest = rest.strip().lstrip("*").strip()
                for part in rest.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    tokens = part.split()
                    if not tokens:
                        continue
                    role = "".join(tokens[1:]) if len(tokens) > 1 else ""
                    devs = tokens[0].replace("，", ",").split(",")
                    for d in devs:
                        d = d.strip()
                        if d:
                            device_roles[d] = role or d
                break
        # 若无注释且可调 LLM：先判级数再分角色（多级/共源共栅兼容）
        if not device_roles and self.client is not None:
            try:
                devices = topology["devices"]
                use_multistage = len(devices) > 9 or "NMCNR" in (netlist_prompt_name or "").upper()
                prompt_name = "sys0_analyze_roles_multistage.txt" if use_multistage else "sys0_analyze_roles.txt"
                sys_analyze = read_sys_prompt(prompt_name)
                messages = [
                    {"role": "system", "content": sys_analyze.get("content", "")},
                    {"role": "user", "content": content}
                ]
                msg = self.get_response(messages)
                out = json.loads(msg.content or "{}")
                roles_raw = out.get("device_roles", {})
                stage_count = out.get("stage_count")
                if use_multistage and stage_count is not None:
                    stage_count = int(stage_count) if isinstance(stage_count, (int, float)) else 1
                else:
                    stage_count = 1
                allowed_1 = {"输入对管", "尾电流管", "负载管", "共源共栅_上臂", "共源共栅_下臂"}
                allowed_3 = allowed_1 | {"第二级_放大管", "第三级_放大管", "负载电流源", "偏置管"}
                allowed = allowed_3 if stage_count == 3 else allowed_1
                cfg = get_topology_config(netlist_prompt_name or "sys0_sleeve.txt")
                fallback = cfg.get("fallback_roles") or {}
                device_roles = {}
                for d in devices:
                    r = roles_raw.get(d) or roles_raw.get(d.upper()) or roles_raw.get(d.lower())
                    if isinstance(r, str) and r.strip() in allowed:
                        device_roles[d] = r.strip()
                    else:
                        device_roles[d] = fallback.get(d) or fallback.get(d.upper()) or fallback.get(d.lower()) or "unknown"
                if any(r != "unknown" for r in device_roles.values()):
                    print("  Agent0 LLM 推理成功 (stage_count=%s, prompt=%s):" % (stage_count, prompt_name), list(device_roles.items())[:5], "..." if len(device_roles) > 5 else "")
                    if out.get("thinking"):
                        thinking_preview = (out["thinking"] or "")[:300]
                        print("  Agent0 拓扑分析思维链:", thinking_preview, "..." if len(out.get("thinking", "")) > 300 else "")
                else:
                    print("  Agent0 LLM 返回空或无效 device_roles，使用 topology_config.fallback_roles 补全")
                    device_roles = {d: fallback.get(d) or fallback.get(d.upper()) or fallback.get(d.lower()) or "unknown" for d in devices}
            except json.JSONDecodeError as e:
                print(f"  Agent0 LLM 输出非 JSON 格式 ({e})，使用默认 'unknown'")
                cfg = get_topology_config(netlist_prompt_name or "sys0_sleeve.txt")
                fallback = cfg.get("fallback_roles") or {}
                device_roles = {d: fallback.get(d) or fallback.get(d.upper()) or fallback.get(d.lower()) or "unknown" for d in topology["devices"]}
            except Exception as e:
                print(f"  Agent0 LLM 调用失败 ({type(e).__name__}: {e})，使用默认 'unknown'")
                cfg = get_topology_config(netlist_prompt_name or "sys0_sleeve.txt")
                fallback = cfg.get("fallback_roles") or {}
                device_roles = {d: fallback.get(d) or fallback.get(d.upper()) or fallback.get(d.lower()) or "unknown" for d in topology["devices"]}
        topology["device_roles"] = device_roles
        return topology


if __name__ == "__main__":
    a = CircuitAgent()
    t = a.invoke()
    print(json.dumps(t, indent=2))
