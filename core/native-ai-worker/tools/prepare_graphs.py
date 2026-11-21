#!/usr/bin/env python3
"""Build the fixed UMX-HQ subgraphs offline; not used by the Rust worker."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

MODEL_SHA = "f27742bb52b24cb039614dc76d0764e279df3eb2a9d6daa82c6daee3369ec747"


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare_graphs.py fixed-model.onnx empty-output-dir")
    blob = Path(sys.argv[1]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != MODEL_SHA:
        raise SystemExit("unexpected model hash")
    dest = Path(sys.argv[2])
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise SystemExit("output directory must be empty")
    graph = onnx.shape_inference.infer_shapes(onnx.load_model_from_string(blob))
    extractor = onnx.utils.Extractor(graph)
    outputs = {
        "pre.onnx": extractor.extract_model(["magnitude"], ["/Tanh_output_0"]).SerializeToString(),
        "post.onnx": extractor.extract_model(["/Tanh_output_0", "/lstm/Reshape_2_output_0", "/Transpose_output_0"], ["vocals_magnitude"]).SerializeToString(),
    }
    initializers = {x.name: x for x in graph.graph.initializer}
    layers = [x for x in graph.graph.node if x.op_type == "LSTM"]
    if len(layers) != 3:
        raise SystemExit("unexpected model")

    def value(name, shape):
        return helper.make_tensor_value_info(name, TensorProto.FLOAT, shape)

    for layer, node in enumerate(layers):
        for index, direction in enumerate(("forward", "reverse")):
            tensors = [numpy_helper.from_array(numpy_helper.to_array(initializers[key])[index:index + 1].copy(), name=name)
                       for key, name in zip(node.input[1:4], ["W", "R", "B"])]
            operation = helper.make_node("LSTM", inputs=["X", "W", "R", "B", "", "H", "C"], outputs=["Y", "Hout", "Cout"], direction=direction, hidden_size=256)
            subgraph = helper.make_graph([operation], "umx-direction", [value("X", ["T", 1, 512]), value("H", [1, 1, 256]), value("C", [1, 1, 256])], [value("Y", ["T", 1, 1, 256]), value("Hout", [1, 1, 256]), value("Cout", [1, 1, 256])], initializer=tensors)
            outputs[f"lstm-{layer}-{direction}.onnx"] = helper.make_model(subgraph, opset_imports=[helper.make_opsetid("", 17)], ir_version=10).SerializeToString()
    manifest = {"model_sha256": MODEL_SHA, "graphs": {}}
    for name, data in outputs.items():
        (dest / name).write_bytes(data)
        manifest["graphs"][name] = hashlib.sha256(data).hexdigest()
    (dest / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
