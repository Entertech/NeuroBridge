// Exercise the production serializer with synthetic values, including NaN.
#define main neurobridge_bridge_entrypoint
#include "../../mac/algorithm_bridge/affective_bridge.cpp"
#undef main
#include <limits>

int main() {
    for (const auto value : {std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::infinity()}) {
        std::ostringstream output;
        bool first = true;
        json_number(output, "hrv", value, first);
        if (output.str() != "\"hrv\":null" || std::string(hrv_state(value)) != "non_finite") return 1;
    }
    if (std::string(hrv_state(0)) != "zero" || std::string(hrv_state(6.5)) != "number") return 2;
    AffectiveAlgorithm algorithm;
    enable_requested_algorithms(algorithm);
    const auto legacy = evaluate(algorithm, "{\"hrRawBase64\":\"UA==\"}");
    if (legacy.find("hrvDiagnostics") != std::string::npos || legacy.find("\"hrv\":0") == std::string::npos) return 3;
    const auto traced = evaluate(algorithm, "{\"hrRawBase64\":\"UA==\",\"hrvDiagnostics\":\"1\"}");
    if (traced.find("\"sdkHrvState\":\"zero\"") == std::string::npos || traced.find("\"serializedHrvState\":\"zero\"") == std::string::npos) return 4;
    const auto skipped = evaluate(algorithm, "{\"hrvDiagnostics\":\"1\"}");
    if (skipped.find("\"sdkHrvState\":\"not_called\"") == std::string::npos) return 5;
    return 0;
}
