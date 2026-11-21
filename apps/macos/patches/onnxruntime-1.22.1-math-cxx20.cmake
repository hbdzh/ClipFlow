# MediaStudio controlled ORT 1.22.1: only the patched source needs C++20.
get_filename_component(MEDIASTUDIO_ORT_MATH_SOURCE
  "${CMAKE_CURRENT_SOURCE_DIR}/../onnxruntime/core/util/math_cpu.cc" ABSOLUTE)
set_property(SOURCE "${MEDIASTUDIO_ORT_MATH_SOURCE}"
  APPEND PROPERTY COMPILE_OPTIONS "-std=c++20")
