#!/bin/bash
# 测试折叠式 OTA PDK 仿真
# 使用方法: cd /home/jzhuang/AnalogLLM/AICAS4Analog && bash scene2/test_folded_pdk.sh

set -e

echo "=== 测试折叠式 OTA PDK 仿真 ==="
echo ""

# 1. 生成一个简单的折叠式 AMP_FOLDED netlist
echo "1. 生成测试用的 AMP_FOLDED netlist..."
cat > scene2/sim/AMP.cir << 'EOF'
.subckt AMP_FOLDED Vinp Vinn VDD VSS Vout
XM_tail net_tail vb_tail VSS VSS sky130_fd_pr__nfet_01v8 L=0.5 W=10.0
XM_in_p net_fold_n Vinp net_tail VSS sky130_fd_pr__nfet_01v8 L=0.5 W=10.0
XM_in_n net_fold_p Vinn net_tail VSS sky130_fd_pr__nfet_01v8 L=0.5 W=10.0
XM_load_p net_fold_p vb_pload VDD VDD sky130_fd_pr__pfet_01v8_lvt L=0.5 W=10.0
XM_load_n net_fold_n vb_pload VDD VDD sky130_fd_pr__pfet_01v8_lvt L=0.5 W=10.0
XM_casc_p_p Vout vb_pcasc net_fold_p VDD sky130_fd_pr__pfet_01v8_lvt L=0.5 W=10.0
XM_casc_p_n Vout vb_pcasc net_fold_n VDD sky130_fd_pr__pfet_01v8_lvt L=0.5 W=10.0
XM_casc_n_p Vout vb_ncasc net_low_p VSS sky130_fd_pr__nfet_01v8 L=0.5 W=10.0
XM_casc_n_n Vout vb_ncasc net_low_n VSS sky130_fd_pr__nfet_01v8 L=0.5 W=10.0
XM_cmfb_p net_low_p vctrl_cmfb VSS VSS sky130_fd_pr__nfet_01v8 L=0.5 W=5.0
XM_cmfb_n net_low_n vctrl_cmfb VSS VSS sky130_fd_pr__nfet_01v8 L=0.5 W=5.0
V_tail vb_tail VSS dc=0.7
V_ncasc vb_ncasc VSS dc=0.7
V_pcasc VDD vb_pcasc dc=0.7
V_pload VDD vb_pload dc=0.7
V_cmfb vctrl_cmfb VSS dc=0.7
.ends AMP_FOLDED
EOF

echo "✓ AMP_FOLDED netlist 已生成"
echo ""

# 2. 运行 IDC 仿真
echo "2. 运行 IDC 仿真..."
cd /home/jzhuang/AnalogLLM/AICAS4Analog
if ngspice -b scene2/sim/folded_IDC_pdk.cir > scene2/sim/test_folded_idc.log 2>&1; then
    echo "✓ IDC 仿真成功"
    echo "  查看结果: tail -10 scene2/sim/test_folded_idc.log"
    tail -5 scene2/sim/test_folded_idc.log
else
    echo "✗ IDC 仿真失败"
    echo "  错误信息:"
    grep -E "Error|could not find|modelname" scene2/sim/test_folded_idc.log | head -5
    exit 1
fi
echo ""

# 3. 运行 AC 仿真
echo "3. 运行 AC 仿真..."
if ngspice -b scene2/sim/folded_AC_pdk.cir > scene2/sim/test_folded_ac.log 2>&1; then
    echo "✓ AC 仿真成功"
    echo "  查看结果: tail -10 scene2/sim/test_folded_ac.log"
    tail -5 scene2/sim/test_folded_ac.log
else
    echo "✗ AC 仿真失败"
    echo "  错误信息:"
    grep -E "Error|could not find|modelname" scene2/sim/test_folded_ac.log | head -5
    exit 1
fi
echo ""

echo "=== 测试完成 ==="
echo "所有仿真均成功！折叠式 OTA PDK 配置正确。"
