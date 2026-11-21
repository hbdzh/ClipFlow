// MediaStudio QA: test the actual ORT conversion functions without a model/session.
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <limits>

namespace onnxruntime::math {
uint16_t floatToHalf(float);
uint16_t doubleToHalf(double);
float halfToFloat(uint16_t);
}

template <typename T>
void record(std::ofstream& out, T value) {
  out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  std::ofstream out(argv[1], std::ios::binary | std::ios::trunc);
  if (!out) return 3;
  // All half representations include both zeros, denormals, infinities and NaNs.
  for (uint32_t bits = 0; bits < 65536; ++bits) {
    const float value = onnxruntime::math::halfToFloat(static_cast<uint16_t>(bits));
    record(out, std::bit_cast<uint32_t>(value));
    record(out, onnxruntime::math::floatToHalf(value));
    record(out, onnxruntime::math::doubleToHalf(static_cast<double>(value)));
  }
  // Boundary values and the neighboring floats cover half underflow, rounding,
  // overflow, signs, normal/subnormal transitions, infinities and NaN payloads.
  constexpr std::array<uint32_t, 24> boundaries = {
      0, 1, 0x007fffff, 0x00800000, 0x33000000, 0x33800000, 0x387fc000,
      0x38800000, 0x3f000000, 0x3f7fe000, 0x3f800000, 0x3f801000,
      0x477fe000, 0x477ff000, 0x47800000, 0x7f7fffff, 0x7f800000,
      0x7f800001, 0x7fc00000, 0x80000000, 0x80800000, 0xbf800000,
      0xff800000, 0xffffffff};
  for (uint32_t boundary : boundaries) {
    for (int offset = -2; offset <= 2; ++offset) {
      const uint32_t bits = boundary + static_cast<uint32_t>(offset);
      const float value = std::bit_cast<float>(bits);
      record(out, onnxruntime::math::floatToHalf(value));
      record(out, onnxruntime::math::doubleToHalf(static_cast<double>(value)));
    }
  }
  uint32_t float_state = 0x4d534f52;
  uint64_t double_state = 0x51533030324f5254ULL;
  for (uint32_t i = 0; i < (1U << 20); ++i) {
    float_state = float_state * 1664525U + 1013904223U;
    double_state = double_state * 6364136223846793005ULL + 1442695040888963407ULL;
    record(out, onnxruntime::math::floatToHalf(std::bit_cast<float>(float_state)));
    record(out, onnxruntime::math::doubleToHalf(std::bit_cast<double>(double_state)));
  }
  for (double boundary : {0.0, -0.0, 0x1p-25, 0x1p-24, 0x1p-14, 1.0,
                          65504.0, 65520.0, std::numeric_limits<double>::max()}) {
    for (double value : {std::nextafter(boundary, -std::numeric_limits<double>::infinity()),
                         boundary,
                         std::nextafter(boundary, std::numeric_limits<double>::infinity())}) {
      record(out, onnxruntime::math::doubleToHalf(value));
    }
  }
  out.close();
  return out ? 0 : 4;
}
