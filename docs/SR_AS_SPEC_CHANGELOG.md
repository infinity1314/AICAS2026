# SR 作为需求的改动记录（可回退）

将 SR（压摆率）加入为需满足的指标并参与输出与达标判断。回退时搜索 `[SR-as-spec]` 或按下列位置还原。

## 改动位置一览

| 文件 | 改动内容 |
|------|----------|
| **scene1/agents/utils.py** | 1) `compute_gap`：当 `specs` 含 `SR_min` 且 `results` 含 `SR` 时，计算 `gap["SR"] = results["SR"] - SR_min`。 2) `is_specs_met`：若 `gap["SR"]` 存在且 < 0 则返回 False。 |
| **scene1/main.py** | 1) 在 `read_simulation_results` 之后：若 `specs` 含 `SR_min`，用 `results["SR"] = Itail/CL`（V/µs）写入 results。 2) `result_delta` 的 key 列表增加 `"SR"`。 3) `gap_delta` 的 key 列表增加 `"SR"`。 |
| **scene1/agents/agent3.py** | `undersatisfied` 构建：若 `last_gap.get("SR")` 存在且 < 0，则追加 `("SR", last_gap["SR"])`。 |
| **scene1/prompt/sys3.txt** | 需求中增加 SR_min；gap 含义中增加 gap.SR；未达标描述中增加 SR；单项不满足策略中增加「仅 SR 不达标」一条。 |
| **scene1/prompt/human3.txt** | 历史说明中增加 gap.SR；量化分析中增加 SR/尾管；未达标策略示例中增加「仅 SR 不足→…」。 |

## 回退步骤

1. 在各文件中搜索 `[SR-as-spec]`，删除或还原对应行/块。
2. **utils.py**：删除 `compute_gap` 中计算 `gap["SR"]` 的 2 行；删除 `is_specs_met` 中 `if "SR" in gap ...` 的 2 行。
3. **main.py**：删除写入 `results["SR"]` 的 4 行；将 `result_delta` 的 key 从 `("Gain", "GBW", "PM", "i(vmeas)", "SR")` 改回不含 `"SR"`；`gap_delta` 的 key 从含 `"SR"` 改回不含。
4. **agent3.py**：删除 `if last_gap.get("SR") ... undersatisfied.append(("SR", ...))` 的 2 行。
5. **sys3.txt / human3.txt**：去掉 SR 相关句及 `[SR-as-spec]` 标记。

## 说明

- 当前 **results["SR"]** 由 main 中按 **Itail/CL** 计算（单位 V/µs），未从仿真文件读取。若后续有 SR 瞬态仿真结果，可在 `read_simulation_results` 或 main 中改为从文件读 SR 并写入 `results["SR"]`。
- specs 中需包含 **SR_min**（如 Agent1 输出或 default_user 中已有），否则不会计算 gap.SR 且不影响 specs_met。
