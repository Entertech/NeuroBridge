#ifndef AC_PLEASURE_HEADER_FILE_GUARD
#define AC_PLEASURE_HEADER_FILE_GUARD
#include "TypeDefine.h"
#include "SessionCache.h"
#include "NumCpp.hpp"
#include "SimpleHandler.h"
namespace ac
{
    struct PleasureTemp
    {
        int index;
        nc::NdArray<double> wearFlagStore;
        basic::affection::handler::PleasureHandlerTemp pleasureTmp;
        vectord pleasureRec;
    };

    struct PleasureReportRes
    {
        vectord pleasureRec;
        double pleasureAvg;
    };

    class PleasureComputing
    {
    public:
        double trigger(const basic::SessionCache &cache);
        PleasureReportRes report();
        PleasureComputing();
        ~PleasureComputing();

    private:
        PleasureTemp temp;
    };

}

#endif