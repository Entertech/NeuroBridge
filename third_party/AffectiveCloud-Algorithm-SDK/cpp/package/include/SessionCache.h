#ifndef CACHE_HEADER_FILE_GUARD 
#define CACHE_HEADER_FILE_GUARD
#include "Data.h"

namespace basic
{
    struct SessionCache
    {
        dsp::EEGQuality eegQuality;
        int eegProgress;
        dsp::EEGPower eeglPower;
        dsp::EEGPower eegrPower; 
        dsp::EEGPower eegPower;

        int hr;
        double hrv;
        dsp::HRQuality hrQuality;
        dsp::HRPower hrPower;
        double hrSyncCor;

        dsp::BCGQuality bcgQuality;
        dsp::RWQuality rwQuality;
        double rr;
    };
    
} // namespace basic
#endif
