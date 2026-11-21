# AI-001 独立 QA

**当前状态（2026-11-10）：**受控 macOS 开发路径的 24 种输入、600 秒循环真实音频、失败与取消及旧 ABI 回归已由独立 QA 验证；具体构建哈希和未验证范围见最终验收。下文涉及尚未接入或待审的描述记录的是早期测试阶段，不表示普通用户路径或正式发行已通过。

本目录由 QA 管理，测试不导入 Core/worker 生产代码，不提供模型权重或第三方素材。`verify_audio.py` 对真实输出做物理检查、全段解码与可选 AC21 独立音频参考比较；`test_verifier.py` 验证比较器会拒绝一采样偏移、截断、非有限值和非 float PCM。音质验收需要自有或授权干净音轨，且不把与参考相加等构造关系当成分离质量。

```sh
python tests/ai_001/test_verifier.py
python tests/ai_001/verify_audio.py --input NORMALIZED_INPUT.wav --output NEW_RESULT_DIR \
  --reference-vocals INDEPENDENT_TORCH_VOCALS.wav --reference-accompaniment INDEPENDENT_TORCH_ACCOMPANIMENT.wav
```

依赖本地 Python NumPy、SoundFile、ffprobe、ffmpeg；版本及许可证单独记录，权重与音频只留本机私有临时目录。`--input` 和独立参考均须位于与 Core 产物相同的 44.1 kHz 双声道 PCM float 采样时间轴；禁止通过相关性搜索任意平移输出使误差达标。传给 `--output` 的目录须严格只有 `vocals.wav` 与 `accompaniment.wav`，非软链接的普通文件。脚本完整解码，不修改输入，记录文件 SHA-256、帧数、流规格与峰值；若指定参考则计算 AC21 归一化 RMS 或静音绝对峰值门限，失败即退出非零。它不代替主观听感、用户源安全或任务状态/清理验证。

真实共享队列开发路径的 ABI、取消、worker 退出和事务/清理已有独立样本证据；10 分钟产品路径仍未验证，不能把研究 worker 结果等同产品验收。此文件与测试预期随 Integration 正式契约而明确，但不得降低 AC16–21 既定门槛。

独立 worker 复验及证据：

```sh
python tests/ai_001/verify_worker.py --python PYTHON --worker STREAM_WORKER --model MODEL --weights ORIGINAL_WEIGHTS --fixture-parent AUTHORIZED_QA_DIR --temp-parent PRIVATE_PARENT
python tests/ai_001/verify_worker_edges.py --python PYTHON --worker STREAM_WORKER --model MODEL --weights ORIGINAL_WEIGHTS --one-second INPUT_1S --thirty-second INPUT_30S --temp-parent PRIVATE_PARENT
python tests/ai_001/verify_worker_60.py --python PYTHON --worker STREAM_WORKER --model MODEL --weights ORIGINAL_WEIGHTS --source INPUT_60S --temp-parent PRIVATE_PARENT
python tests/ai_001/run_runner_probe.py
python tests/ai_001/run_research_flow_qa.py --python PYTHON --worker STREAM_WORKER --model MODEL --input INPUT_44K1_FLOAT_STEREO
```

`verify_worker` 的 fixture-parent 需含事先按 独立样本说明 制作的 `case-1/3/5`。具体本机结果、组件复审和测试局限见 QA 记录。`test_contract.py` 中的合法配置、严格错误、业务预检拒绝遵循待审契约：业务预检用例在产品尚未接入时预期红测，不能声称全通过；`run_e2e.py` 只在共享 Core 实际接入并被审定后运行，要求宿主允许本进程查看自己启动的 worker PID。错误时保留 QA 自己创建的测试目录，先确认子进程及资源回收后才由人处理，不能通过强制删除掩盖失败。


开发根目录与真实生命周期的独立验收脚本：

```sh
python tests/ai_001/test_development_root.py --library LIB --python PYTHON --worker STREAM_WORKER --model MODEL --source INPUT --temp-parent PRIVATE_PARENT
python tests/ai_001/test_contract.py --library LIB --python PYTHON --worker STREAM_WORKER --model MODEL --source INPUT --target-parent PRIVATE_PARENT
python tests/ai_001/run_e2e.py --library LIB --python PYTHON --worker STREAM_WORKER --model MODEL --source INPUT --temp-parent PRIVATE_PARENT --torch-weights ORIGINAL_WEIGHTS
python tests/ai_001/run_lifecycle.py --library LIB --python PYTHON --worker STREAM_WORKER --model MODEL --source INPUT_60S --temp-parent PRIVATE_PARENT --scenario cancel
```

生命周期 `--scenario` 还支持 `crash`、`target_race` 和 `source_change`；要求 macOS 宿主允许 `ps` 和 `lsof` 观察本测试子进程。仅 `crash` 对测试亲自创建且再次核验的 worker 发 SIGKILL；仅变更测试私有源副本和目标。每次新建 0700 root；不确定时保留目录以供诊断。完整结果与边界见 共享队列 QA 记录。
