#ifndef DP_HR_HEADER_FILE_GUARD 
#define DP_HR_HEADER_FILE_GUARD

#include "SessionCache.h"
#include "TypeDefine.h"
#include "HRHandler.h"
namespace dp
{
    struct HRTemp
    {
        int index;
        basic::dsp::hrhandler::HRHandlerTemp hrHandlerTmp;
        vectori hrRec;
        vectord hrvRec;
    };

    struct HRTriggerRes
    {
        int hr;
        double hrv;
    };

    struct HRReportRes
    {
        vectori hrRec;
        vectord hrvRec;
    };

    class HRProgress
    {
        public:
        HRProgress();
        ~HRProgress();
        HRTriggerRes trigger(basic::SessionCache &cache,const vectori &hrData);
        HRReportRes report();
        private:
        HRTemp temp;
    };
}
#endif