// Line-delimited JSON bridge for the locked AffectiveCloud C++ SDK.
// Input comes only from NeuroBridge's local process over stdin; never log raw bytes.

#include "Affective.h"

#include <cctype>
#include <cmath>
#include <cstdint>
#include <exception>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::string json_string_field(const std::string& object, const std::string& key) {
    const std::string quoted_key = "\"" + key + "\"";
    const auto key_offset = object.find(quoted_key);
    if (key_offset == std::string::npos) {
        return {};
    }
    const auto colon = object.find(':', key_offset + quoted_key.size());
    if (colon == std::string::npos) {
        throw std::runtime_error("invalid JSON field: " + key);
    }
    auto value_start = colon + 1;
    while (value_start < object.size() && std::isspace(static_cast<unsigned char>(object[value_start]))) {
        ++value_start;
    }
    if (value_start >= object.size() || object[value_start] != '"') {
        throw std::runtime_error("JSON field is not a string: " + key);
    }
    ++value_start;
    const auto value_end = object.find('"', value_start);
    if (value_end == std::string::npos) {
        throw std::runtime_error("unterminated JSON string: " + key);
    }
    return object.substr(value_start, value_end - value_start);
}

int base64_value(char value) {
    if (value >= 'A' && value <= 'Z') return value - 'A';
    if (value >= 'a' && value <= 'z') return value - 'a' + 26;
    if (value >= '0' && value <= '9') return value - '0' + 52;
    if (value == '+') return 62;
    if (value == '/') return 63;
    return -1;
}

std::vector<uint8_t> base64_decode(const std::string& encoded) {
    std::vector<uint8_t> decoded;
    int accumulator = 0;
    int bits = -8;
    for (const char character : encoded) {
        if (std::isspace(static_cast<unsigned char>(character))) continue;
        if (character == '=') break;
        const int value = base64_value(character);
        if (value < 0) throw std::runtime_error("invalid base64 input");
        accumulator = (accumulator << 6) | value;
        bits += 6;
        if (bits >= 0) {
            decoded.push_back(static_cast<uint8_t>((accumulator >> bits) & 0xff));
            bits -= 8;
        }
    }
    return decoded;
}

void json_number(std::ostream& output, const char* name, double value, bool& first) {
    if (!first) output << ',';
    first = false;
    output << '"' << name << "\":";
    if (std::isfinite(value)) output << std::setprecision(12) << value;
    else output << "null";
}

void json_integer(std::ostream& output, const char* name, int value, bool& first) {
    if (!first) output << ',';
    first = false;
    output << '"' << name << "\":" << value;
}

void json_boolean(std::ostream& output, const char* name, bool value, bool& first) {
    if (!first) output << ',';
    first = false;
    output << '"' << name << "\":" << (value ? "true" : "false");
}

void json_number_array(std::ostream& output, const char* name, const std::vector<double>& values, bool& first) {
    if (!first) output << ',';
    first = false;
    output << '"' << name << "\":[";
    bool first_value = true;
    for (const double value : values) {
        if (!first_value) output << ',';
        first_value = false;
        if (std::isfinite(value)) output << std::setprecision(12) << value;
        else output << "null";
    }
    output << ']';
}

void json_null(std::ostream& output, const char* name, bool& first) {
    if (!first) output << ',';
    first = false;
    output << '"' << name << "\":null";
}

std::string escape_json(const std::string& value) {
    std::string escaped;
    for (const char character : value) {
        if (character == '\\' || character == '"') escaped.push_back('\\');
        if (character == '\n') escaped += "\\n";
        else escaped.push_back(character);
    }
    return escaped;
}

void enable_requested_algorithms(AffectiveAlgorithm& algorithm) {
    algorithm.setEEGEnable(true);
    algorithm.setHREnable(true);
    algorithm.setRelaxationEnable(true);
    algorithm.setAttentionEnable(true);
    algorithm.setPleasureEnable(true);
    algorithm.setSleepEnable(true);
    algorithm.setFlowEnable(true);
    algorithm.setPressureEnable(true);
    algorithm.setCoherenceEnable(true);
    algorithm.setArousalEnable(true);
}

std::string evaluate(AffectiveAlgorithm& algorithm, const std::string& request) {
    const auto eeg = base64_decode(json_string_field(request, "eegRawBase64"));
    const auto hr = base64_decode(json_string_field(request, "hrRawBase64"));
    std::ostringstream result;
    result << "{\"algorithm\":{";
    bool first = true;
    if (!eeg.empty()) {
        const auto values = algorithm.appendEEG(eeg);
        if (!first) result << ',';
        first = false;
        result << "\"eeg\":{\"wave\":{";
        bool first_wave = true;
        json_number_array(result, "left", values.eeg.eeglWave, first_wave);
        json_number_array(result, "right", values.eeg.eegrWave, first_wave);
        json_null(result, "single", first_wave);
        result << "},\"bandPower\":{";
        bool first_band = true;
        json_number(result, "alpha", values.eeg.eegAlphaPower, first_band);
        json_number(result, "beta", values.eeg.eegBetaPower, first_band);
        json_number(result, "theta", values.eeg.eegThetaPower, first_band);
        json_number(result, "delta", values.eeg.eegDeltaPower, first_band);
        json_number(result, "gamma", values.eeg.eegGammaPower, first_band);
        json_number(result, "lowBeta", values.eeg.eegLowBetaPower, first_band);
        json_number(result, "highBeta", values.eeg.eegHighBetaPower, first_band);
        result << "},";
        bool first_quality = true;
        json_number(result, "quality", values.eeg.eegQuality, first_quality);
        result << '}';
        json_number(result, "relaxation", values.relaxation, first);
        json_number(result, "attention", values.attention, first);
        json_number(result, "pleasure", values.pleasure, first);
        if (!first) result << ',';
        first = false;
        result << "\"sleep\":{";
        bool first_sleep = true;
        json_boolean(result, "updated", values.sleep.updateFlag, first_sleep);
        json_number(result, "degree", values.sleep.sleepDegree, first_sleep);
        json_integer(result, "state", values.sleep.sleepState, first_sleep);
        json_integer(result, "stage", values.sleep.sleepStage, first_sleep);
        json_number(result, "spindle", values.sleep.sleepSpindle, first_sleep);
        result << '}';
        if (!first) result << ',';
        first = false;
        result << "\"flow\":{";
        bool first_flow = true;
        json_number(result, "meditation", values.flow.meditation, first_flow);
        json_number(result, "meditationTips", values.flow.meditationTips, first_flow);
        result << '}';
    }
    if (!hr.empty()) {
        const auto values = algorithm.appendHR(hr);
        if (!first) result << ',';
        first = false;
        result << "\"hr\":{";
        bool first_hr = true;
        json_integer(result, "value", values.hr.hr, first_hr);
        json_number(result, "hrv", values.hr.hrv, first_hr);
        result << '}';
        json_number(result, "pressure", values.pressure, first);
        json_number(result, "coherence", values.coherence, first);
        json_number(result, "arousal", values.arousal, first);
    }
    result << "}}";
    return result.str();
}

}  // namespace

int main() {
    AffectiveAlgorithm algorithm;
    enable_requested_algorithms(algorithm);

    std::string request;
    while (std::getline(std::cin, request)) {
        try {
            std::cout << evaluate(algorithm, request) << std::endl;
        } catch (const std::exception& error) {
            std::cout << "{\"algorithm\":{},\"bridgeError\":\"" << escape_json(error.what()) << "\"}" << std::endl;
        }
    }
    return 0;
}
