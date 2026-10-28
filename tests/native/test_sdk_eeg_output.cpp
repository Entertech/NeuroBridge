// Synthetic inputs exercise SDK behavior; they are not device or accuracy evidence.
#include "DSPEEG.h"
#include "EEG.h"
#include "EEGHandler.h"
#include "Pretreat.h"
#include "SCEEG.h"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace basic;
using namespace basic::dsp;

namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

vectord signal(DeviceInfo& device, double seconds, double frequency, double amplitude) {
    // The fixture uses the SDK's device definition, not a gateway sampling-rate assumption.
    vectord samples(static_cast<size_t>(seconds * device.eegFs()));
    const double midpoint = (device.eegMaxVal() + device.eegMinVal()) / 2.0;
    for (size_t i = 0; i < samples.size(); ++i)
        samples[i] = midpoint + amplitude * std::sin(2 * std::acos(-1.0) * frequency * i / device.eegFs());
    return samples;
}

template <typename Wave>
bool has_signal(const Wave& wave) {
    return std::any_of(wave.begin(), wave.end(), [](double x) { return std::isfinite(x) && std::abs(x) > 1e-9; });
}

void handler_contract(DeviceInfo& device) {
    eeghandler::EEGHandlerTemp temp;
    auto warmup = signal(device, 1.0, 50.0, 10000.0);
    auto result = eeghandler::handler(warmup, 4.0, 1.0, &device, temp);
    check(result.quality == NONE && result.power.power == 0, "warmup still waits for the window");
    check(!has_signal(result.eegWave), "warmup waveform remains zero");
    check(temp.eegData.size() == warmup.size(), "warmup retains samples");

    auto noise = signal(device, 4.0, 50.0, 10000.0);
    check(eegLoadCheck(noise, &device), "noise fixture must pass load detection");
    result = eeghandler::handler(noise, 4.0, 1.0, &device, temp);
    check(result.quality == NONE, "noise quality remains NONE rather than -1 or GOOD");
    check(has_signal(result.eegWave), "NONE quality must not blank the waveform");
    check(has_signal(result.featureWave), "NONE quality must not blank the feature waveform");
    check(std::isfinite(result.power.power) && result.power.power > 0, "NONE quality retains band power");
    check(std::isfinite(result.featurePower.power) && result.featurePower.power > 0, "NONE quality retains feature power");

    vectord unloaded(warmup.size(), device.eegMaxVal());
    result = eeghandler::handler(unloaded, 4.0, 1.0, &device, temp);
    check(result.quality == NONE && result.power.power == 0, "failed load detection still rejects data");
    check(!has_signal(result.eegWave) && !has_signal(result.featureWave), "failed load detection still blanks output");
    check(temp.eegData.size() == 0, "failed load detection clears accumulated data");

    auto clean = signal(device, 4.0, 40.0, 100.0);
    result = eeghandler::handler(clean, 4.0, 1.0, &device, temp);
    check(result.quality == GOOD, "good signal keeps its quality rating");
    check(has_signal(result.eegWave) && result.power.power > 0, "good signal still produces output");
}

void processing_contract() {
    DeviceInfoFtV1 device;
    auto samples = signal(device, 4.0, 50.0, 10000.0);
    SessionCache dual_cache{};
    dp::EEGProgress dual;
    auto dual_result = dual.trigger(dual_cache, samples, samples, false);
    check(dual_result.eegQuality == NONE && dual_cache.eegQuality == NONE, "dual processing preserves NONE quality");
    check(has_signal(dual_result.eeglWave) && has_signal(dual_result.eegrWave), "dual processing retains both waveforms");

    SessionCache single_cache{};
    dp::SCEEGProcess single;
    auto single_result = single.trigger(single_cache, samples);
    check(single_result.eegQuality == NONE && single_cache.eegQuality == NONE, "single processing preserves NONE quality");
    check(has_signal(single_result.eegWave), "single processing retains waveform");
}

void dual_waveform_range_contract() {
    DeviceInfoFtV1 device;
    for (double amplitude : {100.0, 30000.0, 1000000.0}) {
        auto left = signal(device, 4.0, 40.0, amplitude);
        auto right = signal(device, 4.0, 25.0, amplitude * 1.5);
        eeghandler::EEGHandlerTemp left_temp, right_temp;
        auto expected_left = eeghandler::handler(left, 4.0, 1.0, &device, left_temp);
        auto expected_right = eeghandler::handler(right, 4.0, 1.0, &device, right_temp);
        if (amplitude > 100000) {
            for (const auto* wave : {&expected_left.eegWave, &expected_right.eegWave}) {
                check(*std::max_element(wave->begin(), wave->end()) > 2000, "fixture must exceed +2000");
                check(*std::min_element(wave->begin(), wave->end()) < -2000, "fixture must exceed -2000");
            }
        }
        // Cover both SDK dual-channel modes; each instance starts with the same history.
        for (bool is_ear : {false, true}) {
            SessionCache cache{};
            dp::EEGProgress process;
            auto result = process.trigger(cache, left, right, is_ear);
            check(result.eeglWave.size() == expected_left.eegWave.size(), "left length must be preserved");
            check(result.eegrWave.size() == expected_right.eegWave.size(), "right length must be preserved");
            for (size_t i = 0; i < result.eeglWave.size(); ++i) {
                check(result.eeglWave[i] >= -2000 && result.eeglWave[i] <= 2000,
                      "left waveform must stay within +/-2000");
                check(result.eegrWave[i] >= -2000 && result.eegrWave[i] <= 2000,
                      "right waveform must stay within +/-2000");
                check(std::abs(result.eeglWave[i] - std::clamp(expected_left.eegWave[i], -2000.0, 2000.0)) <= 0.011,
                      "left waveform must clip only outside +/-2000, preserving existing rounding");
                check(std::abs(result.eegrWave[i] - std::clamp(expected_right.eegWave[i], -2000.0, 2000.0)) <= 0.011,
                      "right waveform must clip only outside +/-2000, preserving existing rounding");
            }
            if (amplitude > 100000) {
                for (const auto* wave : {&result.eeglWave, &result.eegrWave}) {
                    check(*std::max_element(wave->begin(), wave->end()) == 2000, "positive overflow clips to +2000");
                    check(*std::min_element(wave->begin(), wave->end()) == -2000, "negative overflow clips to -2000");
                }
            }
            check(result.eegQuality == std::min(expected_left.quality, expected_right.quality),
                  "waveform range must not change quality grading");
        }
    }
}
} // namespace

int main() {
    try {
        DeviceInfoFtV1 dual_device;
        DeviceInfoEyeShade single_device;
        handler_contract(dual_device);
        handler_contract(single_device);
        processing_contract();
        dual_waveform_range_contract();
        std::cout << "EEG output, quality, warmup and load checks passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
