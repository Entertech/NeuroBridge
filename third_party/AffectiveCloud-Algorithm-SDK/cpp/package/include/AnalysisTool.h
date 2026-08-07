#include "Analysis.h"
#include <cstdint>

namespace basic
{
    namespace tools
    {
        dsp::SingleEEGData singleEEGDataAnalysis(std::vector<uint8_t> eegRawData, int eegPckNum = 30);

        dsp::DoubleEEGData doubleEEGDataAnalysis(std::vector<uint8_t> eegRawData, int eegPckNum = 30);

        dsp::HRData hrDataAnalysis(std::vector<uint8_t> rawData, int pckNum=2);

        dsp::PEPRData peprDataAnalysis(std::vector<uint8_t> rawData, int pckNum = 15);
    } // namespace tools
    
} // namespace basic
