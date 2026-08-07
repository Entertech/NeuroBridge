#ifndef AC_AROUSAL_HEADER_FILE_GUARD
#define AC_AROUSAL_HEADER_FILE_GUARD
#include "SessionCache.h"
#include "TypeDefine.h"
#include "SimpleHandler.h"
#include "NumCpp.hpp"
namespace ac
{
    struct ArousalTemp
    {
        int index;
        nc::NdArray<double> wearFlagStore;
        basic::affection::handler::ArousalHandlerTemp arousalTmp;
        vectord arousalRec;
    };

    struct ArousalReportRes
    {
        vectord arousalRec;
        double arousalAvg;
    };

    class ArousalComputing
    {
    public:
        double trigger(const basic::SessionCache &cache);
        ArousalReportRes report();
        ArousalComputing();
        ~ArousalComputing();

    private:
        ArousalTemp temp;
    };

}

#endif