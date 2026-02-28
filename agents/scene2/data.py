"""
scene1 数据结构：OTA 器件与偏置源（与 agents 一致）。支持套筒式（XM1–XM9）拓扑。
"""
import os

_SCENE1 = os.path.dirname(os.path.abspath(__file__))

# 套筒式 OTA 默认器件名
OTA5_DEVICES = ["XM1", "XM2", "XM3", "XM4", "XM5", "XM6", "XM7", "XM8", "XM9"]


class MOSFETParam:
    def __init__(self, L=0.15, W=1.0, m=1.0):
        self.L = L
        self.W = W
        self.m = m


class MOSFETNet:
    def __init__(self, d, g, s, b):
        self.d, self.g, self.s, self.b = d, g, s, b


class MOSFETCalc:
    def __init__(self, Vgs=0.0, gmid=0.0, idW=0.0, ids=0.0):
        self.Vgs = Vgs
        self.gmid = gmid
        self.idW = idW
        self.ids = ids


class MOSFET:
    def __init__(self, name, type_, net, param, calc_param):
        self.name = name
        self.type = type_
        self.net = net
        self.param = param
        self.calc_param = calc_param

    def update_param(self, attr, value):
        if attr in ("L", "W", "m"):
            setattr(self.param, attr, value)
        elif attr in ("Vgs", "gmid", "idW", "ids"):
            setattr(self.calc_param, attr, value)
        else:
            raise ValueError(f"unknown attr: {attr}")

    def get_param(self, attr):
        if attr in ("L", "W", "m"):
            return getattr(self.param, attr)
        if attr in ("Vgs", "gmid", "idW", "ids"):
            return getattr(self.calc_param, attr)
        if attr == "type":
            return self.type
        raise ValueError(f"unknown attr: {attr}")


class VoltageNet:
    def __init__(self, pos, neg):
        self.pos, self.neg = pos, neg


class VoltageSource:
    def __init__(self, name, net, dc):
        self.name = name
        self.net = net
        self.dc = dc

    def update_dc(self, dc):
        self.dc = dc


mosfet_dict = {}
voltage_source_dict = {}
# SR 放大电路：XSLM10/XSLM11/XSLM12，VSLBP1（与 scene2 一致）
sl_mosfet_dict = {}
sl_voltage_source_dict = {}