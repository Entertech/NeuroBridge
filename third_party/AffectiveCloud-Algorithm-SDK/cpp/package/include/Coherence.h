#ifndef AC_COHERENCE_HEADER_FILE_GUARD
#define AC_COHERENCE_HEADER_FILE_GUARD
#include "SessionCache.h"
#include "TypeDefine.h"
#include "NumCpp.hpp"
#include "SimpleHandler.h"
namespace ac
{
    struct CoherenceTemp
    {
        int index;
        nc::NdArray<double> wearFlagStore;
        basic::affection::handler::CoherenceHandlerTemp coherenceTmp;
        vectord coherenceRec;
    };

    struct CoherenceReportRes
    {
        vectord coherenceRec;
        double coherenceAvg;
        vectori flagRec; //佩戴标志全程记录
        int coherenceDuration; //和谐度时长
    };

    class CoherenceComputing
    {
    public:
        double trigger(const basic::SessionCache &cache);
        CoherenceReportRes report();
        CoherenceComputing();
        ~CoherenceComputing();

    private:
        CoherenceTemp temp;
    };

}

#endif