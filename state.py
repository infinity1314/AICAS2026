"""
scene2 多 agent 流程的全局状态。
彻底切断与外界耦合，确保路径指向正确的物理资源。
"""
import os as _os

# --- 1. 路径修正 ---
# 因为 state.py 在 scene2/ 目录下
_CURRENT_DIR = _os.path.dirname(_os.path.abspath(__file__))
# 仿真输出目录指向 scene2/sim
SIM_DIR: str = _os.path.join(_CURRENT_DIR, "sim")
# 查找表目录指向 scene2/lookup_tables (确保这里有 CSV 文件)
LOOKUP_DIR: str = _os.path.join(_CURRENT_DIR, "lookup_tables")

# --- 2. 核心数据结构 ---
specs: dict = {}
constraints: dict = {}
history: list = []
topology_config: dict = {}

# --- 3. 物理偏置相关变量 ---
# I_ref: 参考基准电流 (uA)
I_ref: float = 100.0
# Itail: 总尾电流 (uA)
Itail: float = 160.0
# scale1: 比例因子，用于从 I_ref 映射到 Itail
scale1: float = 1.6

# 兼容性占位符 (防止 main.py 访问时触发 AttributeError)
scale2: float = 0.0
scale3: float = 0.0
nmcnr_bias: dict = None 

# --- 4. FOM 优化记录 ---
best_fom: float = -1.0
best_params: dict = {}
best_results: dict = {}
best_iter: int = -1