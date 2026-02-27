"""
按所选 sys0（网表 prompt）选取拓扑相关配置：尾电流管、偏置类型、fallback 角色等。
支持：
1. 套筒式五管 OTA (sys0_sleeve.txt)
2. Leung NMCNR 三级运放 (sys0_NMCNR.txt)
"""

# =================================================================
# 1. 套筒式五管 OTA 相关配置
# =================================================================
SLEEVE_OTA5_FALLBACK_ROLES = {
    "XM1": "输入对管", "XM2": "输入对管", 
    "XM3": "负载管", "XM4": "负载管",
    "XM5": "共源共栅_上臂", "XM6": "共源共栅_上臂", 
    "XM7": "共源共栅_下臂", "XM8": "共源共栅_下臂", 
    "XM9": "尾电流管",
}

SLEEVE_SIM_FILES_PDK = ["AMP_AC_pdk.cir", "AMP_IDC_pdk.cir", "AMP_SR_pdk.cir"]
SLEEVE_SIM_FILES_COMPACT = ["AMP_AC.cir", "AMP_IDC.cir", "AMP_SR.cir"]

# =================================================================
# 2. Leung NMCNR 三级运放相关配置
# =================================================================
# 基于信号流分析的默认角色映射（全部使用小写 xm*，与 sys0_NMCNR 网表一致）
NMCNR_FALLBACK_ROLES = {
    # 第一级：折叠共源共栅
    "xm8": "输入对管", "xm9": "输入对管", 
    "xm4": "尾电流管",
    "xm15": "共源共栅_下臂", "xm16": "共源共栅_下臂",
    "xm5": "负载电流源", "xm6": "负载电流源", 
    "xm19": "负载电流源", "xm20": "负载电流源",
    
    # 第二级：放大级
    "xm10": "第二级_放大管", 
    "xm21": "负载电流源", "xm22": "负载电流源",
    "xm7": "负载电流源",
    
    # 第三级：输出级
    "xm23": "第三级_放大管",
    "xm11": "负载电流源",
    
    # 偏置网络
    "xm0": "偏置管", "xm1": "偏置管", "xm2": "偏置管", "xm3": "偏置管",
    "xm12": "偏置管", "xm13": "偏置管", "xm14": "偏置管", 
    "xm17": "偏置管", "xm18": "偏置管"
}

NMCNR_SIM_FILES_PDK = ["NMCNR_AC_pdk.cir", "NMCNR_IDC_pdk.cir", "NMCNR_SR_pdk.cir"]

# =================================================================
# 3. 核心配置映射表
# =================================================================
TOPOLOGY_CONFIG = {
    # 套筒式拓扑配置
    "sys0_sleeve.txt": {
        "label": "套筒式五管 OTA",
        "tail_device": "XM9",
        "bias_type": "sleeve_ota5",
        "fallback_roles": SLEEVE_OTA5_FALLBACK_ROLES,
        "sim_files_pdk": SLEEVE_SIM_FILES_PDK,
        "sim_files_compact": SLEEVE_SIM_FILES_COMPACT,
        # 偏置微调参数
        "bias_offset": {"VBN2": -0.05, "VBP": 0.1},
        "bias_offset_saturation": {"VBP": -0.06},
    },
    
    # NMCNR 三级运放配置
    "sys0_NMCNR.txt": {
        "label": "Leung NMCNR 三级运放",
        "tail_device": "xm4",  # 第一级尾电流源，小写与网表一致
        "bias_type": "nmcnr",
        "fallback_roles": NMCNR_FALLBACK_ROLES,
        # NMCNR 电流比例：降低第三级倍数利于 vout 不顶轨、增益转正；I1:I2:I3 约 1:1:4~6
        "scale2_min_nmcnr": 1.0,
        "scale3_min_nmcnr": 4.0,
        # 分级电流分组：便于 update_ids_result 做 stage-wise 分配
        "current_groups": {
            "stage1_tail": ["xm4"],
            "stage1_main": ["xm8", "xm9", "xm5", "xm6", "xm19", "xm20"],
            "stage2_main": ["xm10", "xm21", "xm22", "xm7"],
            "stage3_main": ["xm23", "xm11"],
            "bias": ["xm0", "xm1", "xm2", "xm3", "xm12", "xm13", "xm14", "xm17", "xm18"],
        },
        "sim_subdir": "sim_NMCNR", # 关键：切换到专门的仿真子目录
        "sim_files_pdk": NMCNR_SIM_FILES_PDK,
        "sim_files_compact": NMCNR_SIM_FILES_PDK, # 三级运放目前仅支持 PDK 仿真
        # 强制 W 上限 500µm（之前为 50µm），防止极端暴走同时给三级放大器更多余量
        "nmcnr_W_max_um": 500.0,
        # Agent3 防暴走：增益未上来前禁止 L 小于此值，避免 DC 不收敛、AC 全 0
        "nmcnr_L_min_um": 0.6,
        # 通过提高负载/输出级 gm_id_min 让查表 idW 更大，从而 W=Ids/idW 自然较小，不靠封顶 W
        "role_gm_id_overrides": {
            "负载电流源": {"gm_id_min": 12},
            "第三级_放大管": {"gm_id_min": 10},
        },
    },
}

DEFAULT_CONFIG = {
    "label": "未知拓扑",
    "tail_device": None,
    "bias_type": "generic",
    "fallback_roles": {},
}

# =================================================================
# 4. 配置获取函数
# =================================================================
def get_topology_config(netlist_prompt_name: str) -> dict:
    """根据所选 sys0 文件名返回拓扑配置（tail_device、bias_type、fallback_roles 等）。"""
    key = netlist_prompt_name if netlist_prompt_name else "sys0_sleeve.txt"
    if not key.endswith(".txt"):
        key = key + ".txt"
        
    cfg = TOPOLOGY_CONFIG.get(key)
    if cfg is None:
        # 如果找不到匹配，返回默认配置并尝试合入基础配置
        cfg = dict(DEFAULT_CONFIG)
    else:
        # 合并默认值，确保字典字段完整
        cfg = {**DEFAULT_CONFIG, **cfg}
        
    return cfg