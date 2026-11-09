# CORE-001 独立 ABI 契约检查

这些检查调用真正的 Rust 动态库，但其作业仅为 CORE-001 mock；不验证真实媒体、原生 UI 或三端运行。

测试在 `core/Cargo.toml` 尚不存在时先写入，随后按 Integration/Core 联合审定的 V0 语义固定失败预期。不得通过放松预期来隐藏实现错误。

## 执行

在本工作树根目录先构建，再给出实际动态库路径：

```sh
cargo build --manifest-path core/Cargo.toml --locked -p media-ffi
python3 tests/core_001/run_qa.py \
  --library core/target/debug/libmedia_ffi.dylib \
  --output tests/core_001/evidence/qa-run.json
```

路径示例针对本机 macOS。其他平台必须按实际产物、原生链接器及运行环境另验；不能从本机结果推断 Windows/HarmonyOS PC 支持。

## 检查范围

- `test_ffi.py`：Python 标准库 ctypes/unittest，显式长度 UTF-8/JSON、schema、空指针/空长度、输入上限、严格字段、重复键、错误输出归零、能力约束、mock 状态/取消、busy 关闭、保留任务上限、独立缓冲所有权、1000 次生命周期、不同 engine 的并行调用。
- `abi_smoke.c`：使用唯一公共头，C11 严格编译、布局检查，链接实际库，运行创建/版本/销毁和销毁后释放独立 buffer。
- `abi_header.cpp`：C++17 公共头与函数签名/布局语法检查；不虚称 C++ 运行或跨平台布局已经通过。
- `run_qa.py`：参数数组启动编译器/测试进程，单进程最长 60 秒；记录命令、退出码、原始输出、运行架构及实际库哈希。

只访问有效 live handle。`ms_buffer_free` 只接收原始 Rust buffer 一次或 `{NULL,0}`。不调用释放后的句柄、不重复 free、不拿伪造非空输入指针触发未定义行为。同一 engine 不并发使用，关闭与其他调用互斥；并行测试使用不同 engine。哨兵只用于新输出结构体存储，验证失败时先归零，从不解引用或释放该哨兵。

压力循环是回归证据，不能单独证明没有内存泄漏。ASan/LSan、真实 worker 取消/清理、panic 注入与平台桥接证据须明确另列实际执行状态。
