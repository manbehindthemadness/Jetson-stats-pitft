#include <cuda.h>
#include <cupti_profiler_host.h>

#include <atomic>
#include <chrono>
#include <csignal>
#include <fstream>
#include <thread>
#include <vector>

#include "pm_sampling.h"

namespace {
constexpr const char *METRIC = "sm__inst_executed_pipe_tensor.sum";
constexpr const char *STATUS = "/run/jetson-stats-pitft/tensor-active";
// Ignore tiny bookkeeping deltas observed during ordinary CUDA context setup.
constexpr double ACTIVE_INSTRUCTION_DELTA = 4096.0;
std::atomic<bool> running{true};

void stop(int) { running = false; }

class TensorMetric {
public:
    void setup(const std::string &chip, std::vector<uint8_t> &availability) {
        CUpti_Profiler_Host_Initialize_Params params{
            CUpti_Profiler_Host_Initialize_Params_STRUCT_SIZE};
        params.profilerType = CUPTI_PROFILER_TYPE_PM_SAMPLING;
        params.pChipName = chip.c_str();
        params.pCounterAvailabilityImage = availability.data();
        CUPTI_API_CALL(cuptiProfilerHostInitialize(&params));
        host_ = params.pHostObject;
    }

    std::vector<uint8_t> config() {
        const char *metrics[] = {METRIC};
        CUpti_Profiler_Host_ConfigAddMetrics_Params add{
            CUpti_Profiler_Host_ConfigAddMetrics_Params_STRUCT_SIZE};
        add.pHostObject = host_;
        add.ppMetricNames = metrics;
        add.numMetrics = 1;
        CUPTI_API_CALL(cuptiProfilerHostConfigAddMetrics(&add));

        CUpti_Profiler_Host_GetConfigImageSize_Params size{
            CUpti_Profiler_Host_GetConfigImageSize_Params_STRUCT_SIZE};
        size.pHostObject = host_;
        CUPTI_API_CALL(cuptiProfilerHostGetConfigImageSize(&size));
        std::vector<uint8_t> image(size.configImageSize);

        CUpti_Profiler_Host_GetConfigImage_Params get{
            CUpti_Profiler_Host_GetConfigImage_Params_STRUCT_SIZE};
        get.pHostObject = host_;
        get.pConfigImage = image.data();
        get.configImageSize = image.size();
        CUPTI_API_CALL(cuptiProfilerHostGetConfigImage(&get));
        return image;
    }

    double evaluate(std::vector<uint8_t> &data, size_t index) {
        const char *metrics[] = {METRIC};
        double value = 0;
        CUpti_Profiler_Host_EvaluateToGpuValues_Params params{
            CUpti_Profiler_Host_EvaluateToGpuValues_Params_STRUCT_SIZE};
        params.pHostObject = host_;
        params.pCounterDataImage = data.data();
        params.counterDataImageSize = data.size();
        params.ppMetricNames = metrics;
        params.numMetrics = 1;
        params.rangeIndex = index;
        params.pMetricValues = &value;
        CUPTI_API_CALL(cuptiProfilerHostEvaluateToGpuValues(&params));
        return value;
    }

    void teardown() {
        CUpti_Profiler_Host_Deinitialize_Params params{
            CUpti_Profiler_Host_Deinitialize_Params_STRUCT_SIZE};
        params.pHostObject = host_;
        CUPTI_API_CALL(cuptiProfilerHostDeinitialize(&params));
    }

private:
    CUpti_Profiler_Host_Object *host_ = nullptr;
};

void mark_active() {
    const auto now = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::ofstream status(STATUS, std::ios::trunc);
    status << now << '\n';
}
}  // namespace

int main() {
    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    DRIVER_API_CALL(cuInit(0));

    std::string chip;
    std::vector<uint8_t> availability;
    CuptiPmSampling::GetChipName(0, chip);
    CuptiPmSampling::GetCounterAvailabilityImage(0, availability);

    TensorMetric metric;
    metric.setup(chip, availability);
    auto config = metric.config();

    CuptiPmSampling sampler;
    sampler.SetUp(0);
    sampler.EnablePmSampling(0);
    // Ten-millisecond hardware samples decoded four times per second are
    // responsive on a 2 Hz display without burning a CPU core on telemetry.
    sampler.SetConfig(config, 2 * 1024 * 1024, 10'000'000);

    std::vector<const char *> metrics{METRIC};
    std::vector<uint8_t> data;
    sampler.CreateCounterDataImage(512, metrics, data);
    sampler.StartPmSampling();

    double previous = 0.0;
    bool have_previous = false;
    while (running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
        sampler.DecodePmSamplingData(data);
        CUpti_PmSampling_GetCounterDataInfo_Params info{
            CUpti_PmSampling_GetCounterDataInfo_Params_STRUCT_SIZE};
        info.pCounterDataImage = data.data();
        info.counterDataImageSize = data.size();
        CUPTI_API_CALL(cuptiPmSamplingGetCounterDataInfo(&info));
        bool active = false;
        for (size_t index = 0; index < info.numCompletedSamples; ++index) {
            const double current = metric.evaluate(data, index);
            if (have_previous && current - previous >= ACTIVE_INSTRUCTION_DELTA)
                active = true;
            previous = current;
            have_previous = true;
        }
        if (active) mark_active();
        sampler.ResetCounterDataImage(data);
    }

    sampler.StopPmSampling();
    sampler.DisablePmSampling();
    sampler.TearDown();
    metric.teardown();
    return 0;
}
