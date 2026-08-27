#include <cuda_fp16.h>
#include <cuda_runtime.h>
#include <mma.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>

using namespace nvcuda;

__global__ void tensor_load(const half *a, const half *b, float *output,
                            unsigned long long cycles) {
    const unsigned int warp = (blockIdx.x * blockDim.x + threadIdx.x) / warpSize;
    const unsigned long long started = clock64();
    wmma::fragment<wmma::matrix_a, 16, 16, 16, half, wmma::row_major> matrix_a;
    wmma::fragment<wmma::matrix_b, 16, 16, 16, half, wmma::col_major> matrix_b;
    wmma::fragment<wmma::accumulator, 16, 16, 16, float> accumulator;
    wmma::load_matrix_sync(matrix_a, a, 16);
    wmma::load_matrix_sync(matrix_b, b, 16);
    wmma::fill_fragment(accumulator, 0.0F);
    while (clock64() - started < cycles)
        wmma::mma_sync(accumulator, matrix_a, matrix_b, accumulator);
    wmma::store_matrix_sync(output + warp * 256, accumulator, 16,
                            wmma::mem_row_major);
}

static bool cuda_ok(cudaError_t result, const char *operation) {
    if (result == cudaSuccess) return true;
    std::fprintf(stderr, "%s: %s\n", operation, cudaGetErrorString(result));
    return false;
}

int main(int argc, char **argv) {
    const int seconds = argc > 1 ? std::atoi(argv[1]) : 3;
    if (seconds < 1 || seconds > 60) return 2;

    cudaDeviceProp properties{};
    if (!cuda_ok(cudaGetDeviceProperties(&properties, 0), "device properties")) return 1;
    int clock_rate_khz = 0;
    if (!cuda_ok(cudaDeviceGetAttribute(&clock_rate_khz, cudaDevAttrClockRate, 0),
                 "clock rate")) return 1;

    constexpr int threads = 128;
    const int blocks = properties.multiProcessorCount * 4;
    const size_t warps = static_cast<size_t>(blocks) * threads / 32;
    half *a = nullptr;
    half *b = nullptr;
    float *output = nullptr;
    if (!cuda_ok(cudaMalloc(&a, 256 * sizeof(half)), "allocate A") ||
        !cuda_ok(cudaMalloc(&b, 256 * sizeof(half)), "allocate B") ||
        !cuda_ok(cudaMalloc(&output, warps * 256 * sizeof(float)), "allocate output"))
        return 1;
    cudaMemset(a, 0x3c, 256 * sizeof(half));
    cudaMemset(b, 0x3c, 256 * sizeof(half));

    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
    const auto cycles = static_cast<unsigned long long>(clock_rate_khz) * 25ULL;
    while (std::chrono::steady_clock::now() < deadline) {
        tensor_load<<<blocks, threads>>>(a, b, output, cycles);
        if (!cuda_ok(cudaGetLastError(), "tensor kernel launch") ||
            !cuda_ok(cudaDeviceSynchronize(), "tensor kernel")) return 1;
    }
    cudaFree(output);
    cudaFree(b);
    cudaFree(a);
    return 0;
}
