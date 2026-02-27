import os
import json
from scene2.agents.utils import read_sys_prompt, read_human_prompt

class SpecAgent:
    def __init__(self):
        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=os.getenv("DASHSCOPE_API_KEY"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        except:
            self.client = None

    def invoke(self, user_input, netlist_content: str = "") -> dict:
        user_str = json.dumps(user_input, ensure_ascii=False)
        if self.client:
            sys1 = read_sys_prompt("sys1.txt")
            human1 = read_human_prompt("human1.txt", user_input=user_str, netlist_content=netlist_content)
            
            completion = self.client.chat.completions.create(
                model="qwen-plus",
                messages=[sys1, human1],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            try:
                return json.loads(completion.choices[0].message.content)
            except:
                print("Agent1 JSON 解析失败，返回空。")
        return {}