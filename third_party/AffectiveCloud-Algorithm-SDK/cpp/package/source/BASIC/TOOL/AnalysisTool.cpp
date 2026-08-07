#include "AnalysisTool.h"

#include <utility>
namespace basic
{
    namespace tools
    {
        dsp::SingleEEGData singleEEGDataAnalysis(std::vector<uint8_t> eegRawData, int eegPckNum)
        {
            return dsp::singleEegDa(eegRawData, eegPckNum);
        }

        dsp::DoubleEEGData doubleEEGDataAnalysis(std::vector<uint8_t> eegRawData, int eegPckNum)
        {
            return dsp::doubleEEGDa(std::move(eegRawData), eegPckNum);
        }

        dsp::HRData hrDataAnalysis(std::vector<uint8_t> rawData, int pckNum)
        {
            return dsp::hrDa(rawData, pckNum);
        }

        dsp::PEPRData peprDataAnalysis(std::vector<uint8_t> rawData, int pckNum)
        {
            return dsp::singlePeprDa(rawData, pckNum);
        }


    }
}
