//
// Created by Enter M1 on 2023/9/4.
//

#ifndef AFFECTIVECPP_PEPR_H
#define AFFECTIVECPP_PEPR_H

#include "SessionCache.h"
#include "TypeDefine.h"
#include "PEPRHandler.h"

namespace dp
{
    struct PEPRTemp
    {
        int index;
        basic::dsp::peprhandler::PEPRHandlerTemp peprHandlerTmp;
        vectori hrRec;
        vectord hrvRec;
        vectori rrRec;
        vectori bcgQualityRec;
        vectori rwQualityRec;
    };

    struct PEPRTriggerRes
    {
        vectord bcgWave;
        vectord rwWave;
        basic::dsp::BCGQuality bcgQuality;
        basic::dsp::RWQuality rwQuality;
        int hr;
        double hrv;
        double rr;
    };

    struct PEPRReportRes
    {
        int hrAvg;
        int hrMax;
        int hrMin;
        vectori hrRec;
        vectori rrRec;
        int rrAvg;
        vectord hrvRec;
        double hrvAvg;
        vectori bcgQualityRec;
        vectori rwQualityRec;

    };

    class PEPRProgress
    {
    public:
        PEPRProgress();
        ~PEPRProgress();
        PEPRTriggerRes trigger(basic::SessionCache &cache,const vectori &peData, const vectori &prData);
        PEPRReportRes report() const;
    private:
        void tempInit();
        PEPRTemp temp;
    };
}

#endif //AFFECTIVECPP_PEPR_H
