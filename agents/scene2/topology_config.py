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

NMCNR_SIM_FILES_PDK = ["NMCNR_IDC_pdk.cir", "NMCNR_AC_pdk.cir", "NMCNR_SR_pdk.cir"]

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
        "nmcnr_I_ref": 5.0,  # 论文/黄金偏置电流 (uA)，与 CURRENT_0_BIAS=5u 一致
        "I_ref": 5.0,         # update_ids_result 用此值做论文比例分配
        "sim_files_pdk": NMCNR_SIM_FILES_PDK,
        "sim_files_compact": NMCNR_SIM_FILES_PDK, # 三级运放目前仅支持 PDK 仿真
        # 强制 W 上限 500µm（之前为 50µm），防止极端暴走同时给三级放大器更多余量
        "nmcnr_W_max_um": 1000.0,
        # Agent3 防暴走：增益未上来前禁止 L 小于此值，避免 DC 不收敛、AC 全 0
        "nmcnr_L_min_um": 0.6,
        # 通过提高负载/输出级 gm_id_min 让查表 idW 更大，从而 W=Ids/idW 自然较小，不靠封顶 W
        # 对于 NMCNR，我们主要依靠 ROLE_RANGES 给出宽容但物理合理的 gm_id 区间；
        # 这里只做轻微下界约束，避免极端弱反型，但不再与黄金 gm_id=3.1 (XM23) 等发生硬冲突。
        "role_gm_id_overrides": {
            "负载电流源": {"gm_id_min": 8},
            "第三级_放大管": {"gm_id_min": 3},
        },
        # 在 TOPOLOGY_CONFIG 的 "sys0_NMCNR.txt" 字典中，加入以下内容：
        # 论文中 m 为等效并联倍数；本流程不写 m，只写单管 W，故仅对「论文里 m 相同」的管子共享 L/W。
        # PMOS: xm0,xm1,xm2,xm3,xm5,xm6,xm7 同 m=4 → 共享；xm4=4* m、xm11=10* m → 不共享，按 Ids 单独算 W。
        # NMOS: xm14 的 m=4，xm12/13/15-18 为 4*4、xm19/20 为 8 → 均与 xm14 不同，全部按 Ids 单独算 W，不共享。
        "parameter_sharing": {
            "xm1": "xm0", "xm2": "xm0", "xm3": "xm0",
            "xm5": "xm0", "xm6": "xm0", "xm7": "xm0",
            "xm9": "xm8",
            "xm22": "xm21"
        },
        # 可工作点默认补偿（与 NMCNR.cir 实测一致）
        "nmcnr_C0_pF": 0.4,
        "nmcnr_C1_pF": 0.2,
        "nmcnr_R0_kOhm": 15.0,
        # 可工作点黄金 L/W/mult：与手工 NMCNR.cir 的 .PARAM 完全一致，存「每指 W」+ mult，网表输出 W= 与 mult= 与参考一致
        # 参考: MOSFET_0_8_W=2.3 M=4; xm4 m=16; xm11 m=40; MOSFET_8_2_W=4 M=4; MOSFET_10_1_W=3.2 M=4;
        #       MOSFET_23_1_W=0.9 M=4; MOSFET_17_7_W=0.47 M=4(或16/32); MOSFET_21_2_W=2 M=4
        "nmcnr_golden_LW_um": {
            "xm0":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm1":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm2":  {"L": 2.0, "W": 2.3, "mult": 4},
            "xm3":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm4":  {"L": 2.0, "W": 2.3, "mult": 16},  "xm5":  {"L": 2.0, "W": 2.3, "mult": 4},
            "xm6":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm7":  {"L": 2.0, "W": 2.3, "mult": 4},   "xm8":  {"L": 2.0, "W": 4.0, "mult": 4},
            "xm9":  {"L": 2.0, "W": 4.0, "mult": 4},   "xm10": {"L": 1.45, "W": 3.2, "mult": 4},  "xm11": {"L": 2.0, "W": 2.3, "mult": 40},
            "xm12": {"L": 0.9, "W": 0.47, "mult": 16}, "xm13": {"L": 0.9, "W": 0.47, "mult": 16}, "xm14": {"L": 0.9, "W": 0.47, "mult": 4},
            "xm15": {"L": 0.9, "W": 0.47, "mult": 16}, "xm16": {"L": 0.9, "W": 0.47, "mult": 16}, "xm17": {"L": 0.9, "W": 0.47, "mult": 16},
            "xm18": {"L": 0.9, "W": 0.47, "mult": 16}, "xm19": {"L": 0.9, "W": 0.47, "mult": 32}, "xm20": {"L": 0.9, "W": 0.47, "mult": 32},
            "xm21": {"L": 1.5, "W": 2.0, "mult": 4},   "xm22": {"L": 1.5, "W": 2.0, "mult": 4},   "xm23": {"L": 2.0, "W": 0.9, "mult": 4},
        },
        # 黄金仿真实测 gm_id（与 nmcnr_golden_LW_um 同工况），用于「黄金 L/gm_id + 查表 W」验证
        "nmcnr_golden_gmid": {
            "xm0": 8.25, "xm1": 8.19, "xm2": 8.19, "xm3": 8.20, "xm4": 7.87, "xm5": 10.48, "xm6": 10.48,
            "xm7": 8.24, "xm8": 8.34, "xm9": 8.34, "xm10": 10.70, "xm11": 8.25,
            "xm12": 17.95, "xm13": 17.95, "xm14": 12.21, "xm15": 19.99, "xm16": 19.99, "xm17": 15.16, "xm18": 15.16,
            "xm19": 16.36, "xm20": 16.36, "xm21": 17.54, "xm22": 17.35, "xm23": 3.40,
        },
        # 每管 Id (µA)：全为 2.5 的倍数，且按 NMCNR_current_relations.md 满足 KCL。Id(xm0)=I0=5µA。
        # 有则 update_ids_result 优先用此表；网表 CURRENT_0_BIAS = nmcnr_I0_ua（参考 5µA）。
        "nmcnr_actual_ids_ua": {
            "xm0": 5, "xm1": 5, "xm2": 5, "xm3": 5, "xm4": 20, "xm5": 2.5, "xm6": 2.5,
            "xm7": 5, "xm8": 10, "xm9": 10, "xm10": 5, "xm11": 50,
            "xm12": 10, "xm13": 5, "xm14": 5, "xm15": 2.5, "xm16": 2.5, "xm17": 10, "xm18": 5,
            "xm19": 12.5, "xm20": 12.5, "xm21": 50, "xm22": 55, "xm23": 50,
        },
        # net013：Id(xm0)=I0（仅 xm0 漏在 net013），I0 为偏置参考电流 5µA
        "nmcnr_I0_ua": 5,
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