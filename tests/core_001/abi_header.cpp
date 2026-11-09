#include "media_core_v0.h"
#include <type_traits>

static_assert(std::is_standard_layout<ms_buffer>::value, "C-compatible buffer layout");
static_assert(offsetof(ms_buffer, ptr) == 0, "pointer offset");
static_assert(sizeof(decltype(ms_buffer::len)) == sizeof(size_t), "size_t length");
static_assert(std::is_same<decltype(&ms_engine_create), int32_t (*)(const uint8_t *, size_t, ms_engine **)>::value,
              "C++ sees the same create signature");
static_assert(std::is_same<decltype(&ms_buffer_free), void (*)(ms_buffer)>::value,
              "buffer free passes the struct by value");
int main() { return MS_OK; }
